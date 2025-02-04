"""
Build code

A build is a repo state that was triggered to be run in multiple jobs.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
import typing

from .config import CFG
from .job import Job
from .project import Project
from .update import (BuildState, BuildSource, QueueActions, JobState,
                     BuildJobCreated, JobUpdate, RegisterActions,
                     Update, GeneratedUpdate, JobEmergencyAbort)
from .watchable import Watchable
from .watcher import Watcher

if typing.TYPE_CHECKING:
    from typing import Sequence


class Build(Watchable, Watcher):
    """
    A Build is a request to process a specific state of a repo, identified
    by the commit hash. The build then launches multiple jobs, as required
    by the associated project.

    The Jobs are then executed on some justin instance, where all the steps
    for the job are run.
    """

    def __init__(self, project: Project, commit_hash: str):
        super().__init__()

        if not (commit_hash.isalnum() and len(commit_hash) == 40):
            raise ValueError("bad commit SHA: " + repr(commit_hash))

        self.commit_hash = commit_hash

        if not isinstance(project, Project):
            raise TypeError("invalid project type: %s" % type(project))
        self.project = project

        # No more jobs required to perform for this build.
        # If the build has been completed, stores the unix timestamp (float).
        # else, it is None.
        self.completed = None

        # Was the finish() function called?
        self.finished = False

        # If completed is true, but this is false,
        # then we can load updates from disk.
        self._all_loaded = False

        # The Queue where this build was put in
        self._queue = None

        # gathered sources of this build.
        self.sources: set[BuildSource] = set()

        # List of build status update JSON objects.
        # Job specific updates are stored in the appropriate job.
        self.updates: list[Update] = list()

        # jobs required for this build to succeeed.
        # job_name -> Job
        self.jobs: dict[str, Job] = dict()

        # job status collections
        self.jobs_pending: set[Job] = set()
        self.jobs_succeeded: set[Job] = set()
        self.jobs_errored: set[Job] = set()

        # jobs that were once running
        # (we remember them to re-create jobs when this build is reconstructed)
        # {job_name -> vm_name, ...}
        self._jobs_to_reconstruct: dict[str, str] = dict()

        # URL where the repo containing this commit can be cloned from
        # during the 'building' phase.
        self.clone_url = None

        project_path = CFG.output_folder / self.project.name

        # $dir/$project/jobs/hash[:3]/hash
        # -> 4096 folders with 2**148 files max, as long as git uses sha1
        self.path = (project_path / "jobs" /
                     self.commit_hash[:3] /  # -> 4096 folders with 2**148 files max
                     self.commit_hash[3:])

        # Branch available in the clone url where the commit
        # to be built is in.
        # If set, we can clone this branch.
        # If not set, we clone the whole repo.
        self.branch = None

        if CFG.dyn_frontend_ssl:
            dyn_ssl = "wss"
        else:
            dyn_ssl = "ws"

        if dyn_ssl:
            omit_port = CFG.dyn_frontend_port == 443
        else:
            omit_port = CFG.dyn_frontend_port == 80

        ws_port = ""
        if not omit_port:
            ws_port = ":%d" % CFG.dyn_frontend_port

        # info url of this build (stored in a link)
        self.target_url = (
            f"{CFG.mandy_url}?wsurl={dyn_ssl}://"
            f"{CFG.dyn_frontend_host}{ws_port}/ws"
            f"&staticurl={CFG.static_url}"
            f"&project={self.project.name}"
            f"&hash={self.commit_hash}"
        )

        self._update_lock = asyncio.Lock()

    async def load(self, preferred_job_name: str | None = None):
        """
        requests to maybe load this job from storage.
        the job can either be on-disk, in-memory or just in progress.
        """

        if CFG.args.volatile:
            return

        try:
            # check if the build was completed (ok/failed) already.
            self.completed = self.path.joinpath("_completed").stat().st_mtime
        except FileNotFoundError:
            pass

        if self.completed and not self._all_loaded:
            await self._load_from_fs()
            await self._reconstruct_jobs(preferred_job_name)

    async def _load_from_fs(self):
        """
        Reconstruct this build from updates stored on disk.

        the BuildJobCreated updates will trigger job recreations in on_send_update.
        """

        if self._all_loaded:
            raise Exception("already loaded build is loading again")

        updates_file = self.path.joinpath("_updates")

        if not updates_file.is_file():
            return

        with updates_file.open() as updates_file:
            for json_line in updates_file:
                await self.send_update(Update.construct(json_line),
                                       reconstruct=True)

        self._all_loaded = True

    def purge_fs(self):
        """
        Remove the whole build directory (and create a fresh one).
        """

        if CFG.args.volatile:
            return

        try:
            shutil.rmtree(str(self.path))
        except FileNotFoundError:
            pass

    async def _reconstruct_jobs(self, preferred_job_name: str | None = None):
        """
        Reconstruct previously active jobs.
        The info what jobs to reconstruct stems from the build's stored updates:
        BuildJobCreated is converted to the self._jobs_to_reconstruct info.
        """

        # if a job preference was given, reconstruct it first.
        if preferred_job_name:
            preferred_job = self._jobs_to_reconstruct.pop(preferred_job_name, None)
            if preferred_job is not None:
                jobs = {preferred_job_name: preferred_job, **self._jobs_to_reconstruct}
            else:
                logging.debug("preferred job '%s' not found", preferred_job_name)
                jobs = self._jobs_to_reconstruct

        else:
            jobs = self._jobs_to_reconstruct

        for job_name, vm_name in jobs.items():
            if job_name in self.jobs:
                raise Exception(f"Job to reconstruct {job_name!r} is already registered")

            job = Job(self, self.project, job_name, vm_name)

            # bypass the `RegisterActions`-message and register directly
            await job.register_to_build()

            # reconstruct this job
            # this emits the updates and we get them in on_update
            job_reconstructed = await job.load()
            if not job_reconstructed:
                raise Exception("job could not be reconstructed")

        self._jobs_to_reconstruct.clear()

    async def merge_job_updates(self, job_name: str, merged_updates: Sequence[Update]):
        """
        job_name finished, and has merged updates.
        for replay of merged messages, build needs to store those as well.
        """

        async with self._update_lock:

            new_updates: list[Update] = list()

            # remove all updates from the job that sent us merged ones.
            # so we can replace them.
            for update in self.updates:
                if isinstance(update, JobUpdate) and update.job_name == job_name:
                    continue

                new_updates.append(update)

            new_updates.extend(merged_updates)
            self.updates = new_updates

    async def set_state(self, state, text, timestamp=None):
        """ set this build state """
        await self.send_update(BuildState(self.project.name, self.commit_hash,
                                          state, text, timestamp))

    async def add_source(self, clone_url, repo_url=None, user=None, branch=None,
                         comment=None):
        """
        Store the build source settings, namely the repo url.
        """
        # a primitive duplicate-source filter
        for source in self.sources:
            if (source.repo_url == repo_url and
                source.branch == branch):
                return

        await self.send_update(BuildSource(
            clone_url=clone_url,   # Where to clone the repo from
            repo_url=repo_url,     # Website of the repo
            author=user,           # User that triggered the build
            branch=branch,         # Branchname of this build
            comment=comment,
        ))

    def requires_run(self) -> bool:
        """
        Returns true if this build requires a run.
        """

        return (not self.completed or
                self._jobs_to_reconstruct)

    async def run(self, queue):
        """
        The actions of this build must now add themselves
        to the given queue.
        This is called to actually start the build and its jobs.

        run is called by the build trigger,
        not a passive subscriber like the httpd.
        """

        if self._all_loaded:
            raise Exception(f"attempted to run already loaded build {self}")
        self._all_loaded = True

        # memorize the queue
        self._queue = queue

        if self.completed is None:
            # TODO send more fine-grained build progress states
            await self.set_state("waiting", "enqueued")

        # prepare the output folder
        if not self.completed:
            self.purge_fs()

            # restore updates like the build sources
            # to the file storage.
            for update in self.updates:
                self._save_update(update)

        # add jobs and other actions defined by the project.
        # some of the actions may be skipped if the build is completed already.
        await self.project.attach_actions(self, self.completed)

        # tell all watchers (e.g. jobs) that they were attached,
        # and may now register themselves at this build.
        await self.send_update(RegisterActions())

        # notify all watchers (e.g. jobs) that they should run their actions.
        # jobs use this as the signal to reconstruct themselves,
        # or, if they're "new" jobs, to enqueue their execution.
        # this will trigger a call to Job.run
        await self.send_update(QueueActions(self.commit_hash, queue,
                                            self.project.name))

    async def create_job(self, job_name: str, vm_name: str) -> Job:
        """
        creates a job, triggered by when a projects' `JobAction` are attached for a build.
        it's not yet registered, this happens by the build emitting RegisterActions
        which the job then uses to call `register_job`.
        """
        if self.finished:
            raise Exception("job created after build was finished!")

        new_job = Job(self, self.project, job_name, vm_name)
        await self.send_update(BuildJobCreated(job_name, vm_name))

        return new_job

    def register_job(self, job):
        """
        Registers a job that is run for this build.

        This is called from a Job when we send the `RegisterActions` update.
        Or, on reconstruction, the build calls job.register_to_build().
        """

        if job.name in self.jobs:
            # the job is already registered if the build is reconstructed
            # and a job in the reconstruction was attached by
            # project settings previously!
            return

        # store the job in the build.
        self.jobs[job.name] = job

        # put it into pending, even if it's actually finished.
        # we'll soon get a JobState update which will put
        # it into the right queue.
        self.jobs_pending.add(job)

    async def on_watcher_registered(self, watcher):
        """
        Some observer was registered to this build, so we send
        all previous updates to it.
        """

        for update in self.updates:
            await watcher.on_update(update)

    def on_send_update(self, update, reconstruct=False):
        """ Called before this update is sent to all watchers. """

        if isinstance(update, BuildSource):
            self.sources.add(update)
            self.clone_url = update.clone_url
            self.branch = update.branch

        elif isinstance(update, BuildJobCreated):
            # recreate all missing jobs that were active
            # when the build ran.
            if reconstruct:
                if update.job_name in self._jobs_to_reconstruct:
                    raise Exception(f"duplicate job name {update.job_name!r}")
                logging.debug("register job %s for reconstruction", update.job_name)
                self._jobs_to_reconstruct[update.job_name] = update.vm_name

        # stored the update to be sent to a new subscriber
        self.updates.append(update)

        # if reconstructing, we're just reading from the disk
        store_to_disk = not reconstruct

        # don't serialize generated updates to disk
        # when we'll reconstruct from disk,
        # those will be generated again.
        if isinstance(update, GeneratedUpdate):
            store_to_disk = False

        if not store_to_disk or CFG.args.volatile:
            # don't write the update to the job storage
            return

        self._save_update(update)

    def _save_update(self, update):
        """
        Save an update to disk for later reconstruction.
        """

        if CFG.args.volatile:
            return

        # whitelist for stored build updates
        if not isinstance(update, (BuildSource, BuildJobCreated)):
            return

        if not self.path.is_dir():
            self.path.mkdir(parents=True)

        # append this update to the build updates file
        # TODO perf: don't open _updates on each update!
        with self.path.joinpath("_updates").open("a") as ufile:
            ufile.write(update.json() + "\n")

    async def on_update(self, update):
        """
        Received message from somewhere,
        now relay it to watchers that may want to see it.
        """

        # shall we relay the message to the watchers of the build?
        distribute = False

        # all job updates are distributed.
        if isinstance(update, JobUpdate):
            distribute = True

            if isinstance(update, JobEmergencyAbort):
                # only send the update if it was not a emergency abort!
                # this prevents that an exception in this call prevents
                # reaching the finish stuff below.
                distribute = False

        # send the update e.g. to mandy
        if distribute:
            # exclude jobs from receiving the update
            await self.send_update(
                update,
                lambda subscriber: isinstance(subscriber, Job)
            )

        # track job state updates:
        if isinstance(update, JobState):
            # TODO: if one step of a job failed,
            # the build must wait until the remaining steps are run.
            if update.job_name not in self.jobs:
                raise Exception("unknown state update for job '%s' "
                                "in project '%s'" % (update.job_name,
                                                     self.project.name))

            # a job reports its status:
            job = self.jobs[update.job_name]

            if job in self.jobs_pending:
                if update.is_finished():
                    self.jobs_pending.remove(job)

                if update.is_succeeded():
                    self.jobs_succeeded.add(job)

                if update.is_errored():
                    self.jobs_errored.add(job)
            else:
                # update for a non-pending job.
                # this happens e.g. for further failure
                # notifications from chantal.
                pass

            if not self.jobs_pending:
                await self._finish()

    async def _finish(self):
        """ no more jobs are pending  """
        if self.finished:
            # finish message already sent.
            logging.warning("build %s finished again, wtf?", self)
            return

        logging.debug("build %s finished", self)

        if self._queue is not None:
            self._queue.remove_build(self)

            # we're no longer enqueued.
            self._queue = None

        try:
            # TODO: we may wanna have allowed-to-fail jobs.
            if self.jobs_succeeded == set(self.jobs.values()):
                count = len(self.jobs)
                await self.set_state("success", "%d job%s succeeded" % (
                    count, "s" if count > 1 else ""))

            else:
                # we had some unsucessful jobs:

                if self.jobs_errored:
                    # one or more jobs errored.
                    count = len(self.jobs_errored)
                    await self.set_state("error", "%d job%s errored" % (
                        count, "s" if count > 1 else ""))

                else:
                    # one or more jobs failed.
                    count = len(self.jobs)
                    await self.set_state("failure", "%d/%d job%s failed" % (
                        count - len(self.jobs_succeeded),
                        count, "s" if count > 1 else ""))
        finally:
            # build is completed now!
            if not CFG.args.volatile:
                self.path.joinpath("_completed").touch()
            self.completed = time.time()
            self.finished = True

    async def abort(self):
        """ Abort this build """

        if self.finished:
            return

        if self._queue is not None:
            for job in self.jobs_pending.copy():
                await self._queue.cancel_job(job)

        await self._finish()
