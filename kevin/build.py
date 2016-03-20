"""
Build code

A build is a repo state that was triggered to be run in multiple jobs.
"""

import datetime
import shutil
from pathlib import Path

from .config import CFG
from .project import Project
from .update import (Update, BuildState, BuildSource, Enqueued, JobState,
                     GeneratedUpdate, JobCreated, JobUpdate, ActionsAttached)
from .watcher import Watchable, Watcher


# stores known builds by (project, hash) -> build
BUILD_CACHE = dict()


def new_build(project, commit_hash, create_new=True):
    """ create a new build or return it from the cache """
    cache_key = (project, commit_hash)

    if cache_key in BUILD_CACHE:
        # already known build.
        return BUILD_CACHE[cache_key]

    else:
        # this tries loading from filesystem
        newbuild = Build(project, commit_hash)

        # store it as known build?
        if newbuild.completed or create_new:
            BUILD_CACHE[cache_key] = newbuild
        else:
            newbuild = None

        return newbuild


def get_build(project, commit_hash):
    """
    return an existing build from the cache
    return None if it coultn't be found.
    """

    return new_build(project, commit_hash, create_new=False)


class Build(Watchable, Watcher):
    """
    A Build is a request to process a specific state of a repo, identified
    by the commit hash. The build then launches multiple jobs, as required
    by the associated project.

    The Jobs are then executed on some falk instance, where all the steps
    for the job are run.
    """

    def __init__(self, project, commit_hash):
        super().__init__()

        if not (commit_hash.isalnum() and len(commit_hash) == 40):
            raise ValueError("bad commit SHA: " + repr(commit_hash))

        self.commit_hash = commit_hash

        if not isinstance(project, Project):
            raise ValueError("invalid project type: %s" % type(project))
        self.project = project

        # No more jobs required to perform for this build
        self.completed = False

        # Was the finish() function called?
        self.finished = False

        # were the build actions attached?
        self.actions_attached = False

        # were the actions notified of the enqueuing?
        self.actions_notified = False

        # Did this build exist before?
        self.created = False

        # The Queue where this build was put in
        self.queue = None

        # Special information storage, for example the status update url,
        # or other custom used by triggers and actions.
        self.info = dict()

        # gathered sources of this build.
        self.sources = set()

        # List of build status update JSON objects.
        # Job specific updates are stored in the appropriate job.
        self.updates = list()

        # jobs required for this build to succeeed.
        # job_name -> Job
        self.jobs = dict()

        # job status collections
        self.jobs_pending = set()
        self.jobs_succeeded = set()
        self.jobs_errored = set()

        # URL where the repo containing this commit can be cloned from
        # during the 'building' phase.
        self.clone_url = None

        # folder/project/jobs/hash[:3]/hash/
        self.relpath = Path(
            self.project.name,
            "jobs",
            self.commit_hash[:3],  # -> 4096 folders with 2**148 files max
            self.commit_hash
        )

        # storage path for the job output
        self.path = CFG.output_folder / self.relpath

        # info url of this build, on the dyn server for now.
        # later, reference to mandy at web_url.
        self.target_url = "%s?project=%s&hash=%s" % (
            CFG.dyn_url, self.project.name, self.commit_hash)

        # try to reconstruct the build state from filesystem
        self.load_from_fs()

        if not CFG.args.volatile:
            if not self.path.is_dir():
                self.path.mkdir(parents=True)

        # create and attach all actions defined in the project.
        # this creates and registers the watchers.
        # even if the build is finished: that way,
        # they receive the updates again.
        #
        # the attachment should happen whenever the build
        # state is recreated, even if the build was completed already.
        if not self.actions_attached:
            self.project.attach_actions(self)
            self.actions_attached = True

            # notify all watchers that all actions (which are watchers)
            # are now attached.
            # this e.g. triggers that jobs register at the build.
            self.send_update(ActionsAttached())

    def load_from_fs(self):
        """
        set up this build from the filesystem.
        """

        # only reconstruct if we wanna use the local storage
        if CFG.args.volatile:
            return

        # Check the current status of the job.
        self.created = self.path.joinpath("_created").is_file()
        self.completed = self.path.joinpath("_completed").is_file()

        if not self.completed:
            # make sure that there are no remains
            # of previous aborted build.
            try:
                shutil.rmtree(str(self.path))
            except FileNotFoundError:
                pass

    def set_state(self, state, text, time=None):
        """ set this build state """
        self.send_update(BuildState(self.project.name, self.commit_hash,
                                    state, text, time))

    def add_source(self, clone_url, repo_url=None, user=None, branch=None,
                   comment=None):
        """
        Store the build source settings, namely the repo url.
        """

        self.on_update(BuildSource(
            clone_url=clone_url,   # Where to clone the repo from
            repo_url=repo_url,     # Website of the repo
            author=user,           # User that triggered the build
            branch=branch,         # Branchname of this build
            comment=comment,
        ))

    def on_enqueue(self, queue):
        """
        Run when the build was added to the processing queue.

        Let this build run all the actions it should.
        Just trigger the enqueue action so the actions place
        themselves in the requested queue.
        """

        # memorize the queue
        self.queue = queue

        if self.completed:
            # no more processing needed.
            if self.queue:
                self.queue.remove_build(self)

        else:
            # create build state folder
            if not CFG.args.volatile:
                with self.path.joinpath("_created").open("w") as datefile:
                    now = datetime.datetime.now()
                    datefile.write(now.isoformat())

        if not self.actions_notified:
            if not self.completed:
                self.set_state("pending", "enqueued")

            # notify all watchers (e.g. jobs) that the job now is enqueued
            self.send_update(Enqueued(self.commit_hash, queue,
                                      self.project.name))
            self.actions_notified = True

    def register_job(self, job):
        """
        Registers a job that is run for this build.
        """
        # some job was notified by this build and now
        # says "hey i'm created now."
        self.jobs[job.name] = job

        # put it into pending, even if it's actually finished.
        # we'll soon get a JobState update which will put
        # it into the right queue.
        self.jobs_pending.add(job)

    def on_watch(self, watcher):
        """
        Registers a watcher object to this build.

        The watcher's on_update() member method will be called for every
        update that ever was and ever will be until unwatch() below is
        called.
        """

        # send all previous updates to the watcher
        for update in self.updates:
            watcher.on_update(update)

    def on_send_update(self, update):
        """ When an update is to be sent to all watchers """

        if update == StopIteration:
            return

        self.updates.append(update)

    def on_update(self, update):
        """ Received message from somewhere """

        if isinstance(update, JobCreated):
            if self.finished:
                raise Exception("job created after build was finished!")

        if isinstance(update, JobUpdate):
            self.send_update(update)

        if isinstance(update, BuildSource):
            # TODO: detect duplicate sources? conflicts?
            self.sources.add(update)
            self.clone_url = update.clone_url

        elif isinstance(update, JobState):
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

            if len(self.jobs_pending) == 0:
                self.finish()

    def finish(self):
        """ no more jobs are pending  """

        if self.finished:
            # finish message already sent.
            return

        if self.queue:
            self.queue.remove_build(self)

            # we're no longer enqueued.
            self.queue = None

        # TODO: we may wanna have allowed-to-fail jobs.
        if self.jobs_succeeded == set(self.jobs.values()):
            count = len(self.jobs)
            self.set_state("success", "%d job%s succeeded" % (
                count, "s" if count > 1 else ""))

        else:
            # we had some unsucessful jobs:

            if len(self.jobs_errored) > 0:
                # one or more jobs errored.
                count = len(self.jobs_errored)
                self.set_state("error", "%d job%s errored" % (
                    count, "s" if count > 1 else ""))

            else:
                # one or more jobs failed.
                count = len(self.jobs)
                self.set_state("failure", "%d/%d job%s failed" % (
                    count - len(self.jobs_succeeded),
                    count, "s" if count > 1 else ""))

        # build is completed now!
        if not CFG.args.volatile:
            self.path.joinpath("_completed").touch()
        self.completed = True
        self.finished = True

    def abort(self):
        """ Abort this build """

        if self.finished:
            return

        if self.queue:
            for job in self.jobs_pending.copy():
                self.queue.cancel_job(job)

        self.finish()
