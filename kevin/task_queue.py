"""
Task queuing for Kevin.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import typing

if typing.TYPE_CHECKING:
    from .build import Build
    from .job import Job
    from .job_manager import JobManager


class TaskQueue:
    """
    Queue to manage pending builds and jobs.
    """

    def __init__(self,
                 loop: asyncio.AbstractEventLoop,
                 job_manager: JobManager,
                 max_running: int,
                 max_queued: int):

        # event loop
        self._loop = loop

        # job distribution
        self._job_manager = job_manager

        # builds that should be run
        self._build_queue: asyncio.Queue[Build] = asyncio.Queue(maxsize=max_queued)

        # all Builds that are pending
        self._pending_builds: set[Build] = set()

        # build_id -> Build
        self._build_ids: dict[int, Build] = dict()

        # jobs that should be run
        self._job_queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=max_queued)

        # running jobs
        # job -> job_task
        self._jobs: dict[Job, asyncio.Task[Job]] = dict()

        # was the execution of the queue cancelled
        self._cancelled = False

        # number of jobs running in parallel
        self._max_running: int = max_running

    async def run(self):
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.process_builds())
                tg.create_task(self.process_jobs())
        except asyncio.CancelledError:
            await self.cancel()
            raise

    async def process_builds(self):
        """
        process items from the build queue
        """
        while True:
            build = await self._build_queue.get()
            await build.run(self)

    async def add_build(self, build):
        """
        Add a build to be processed.
        Called from where a new build was created and should now be run.
        """

        if build in self._pending_builds:
            return

        if build.requires_run():
            logging.info("[queue] adding build: [\x1b[33m%s\x1b[m] @ %s",
                         build.commit_hash,
                         build.clone_url)

            self._pending_builds.add(build)
            self._build_ids[build.commit_hash] = build

            # the build shall now run.
            # this is done by adding jobs to this queue.
            await build.run(self)

    def remove_build(self, build):
        """ Remove a finished build """
        del self._build_ids[build.commit_hash]
        self._pending_builds.remove(build)

    async def abort_build(self, build_id):
        """ Abort a running build by aborting all pending jobs """

        build = self._build_ids.get(build_id)

        if build:
            if not build.completed:
                await build.abort()

    def is_pending(self, commit_hash):
        """ Test if a commit hash is currently being built """
        # TODO: what if a second project wants the same hash?
        #       we can't reuse the build then!
        return commit_hash in self._build_ids.keys()

    async def add_job(self, job):
        """ Add a job to the queue """

        if job.completed:
            # don't enqueue completed jobs.
            return

        try:
            # place the job into the pending list.
            self._job_queue.put_nowait(job)

        except asyncio.QueueFull:
            await job.error("overloaded; job was dropped.")

    async def process_jobs(self):
        """ process jobs from the queue forever """

        while not self._cancelled:

            if self._job_queue.empty():
                logging.info("[queue] \x1b[32mWaiting for job...\x1b[m")

            # fetch new job from the queue
            job = await self._job_queue.get()

            logging.info("[queue] \x1b[32mProcessing job\x1b[m %s.%s for "
                         "[\x1b[34m%s\x1b[m]...",
                         job.build.project.name,
                         job.name,
                         job.build.commit_hash)

            # spawn the build job.
            # the job will be distributed to one of the runners.
            job_task = self._loop.create_task(job.run(self._job_manager))

            self._jobs[job] = job_task

            # register the callback when the job is done
            job_task.add_done_callback(functools.partial(
                self.job_done, job=job))

            # wait for jobs to complete if there are too many running
            # this can be done very dynamically in the future.
            if len(self._jobs) >= self._max_running or self._cancelled:
                logging.info("[queue] runlimit of %d reached, "
                             "waiting for completion...", self._max_running)

                # wait until a "slot" is available, then the next job
                # can be processed.
                await asyncio.wait(
                    self._jobs.values(),
                    return_when=asyncio.FIRST_COMPLETED)

    def job_done(self, task, job):
        """ callback for finished jobs """
        del task  # unused

        logging.info("[queue] Job %s.%s finished for [\x1b[34m%s\x1b[m].",
                     job.build.project.name,
                     job.name,
                     job.build.commit_hash)

        try:
            del self._jobs[job]
        except KeyError:
            logging.exception("\x1b[31mBUG\x1b[m: job %s not in running set", job)

    async def cancel(self):
        """ cancel all running jobs """

        to_cancel = len(self._jobs)
        self._cancelled = True

        if to_cancel == 0:
            return

        logging.info("[queue] cancelling running jobs...")

        for job_fut in self._jobs.values():
            job_fut.cancel()

        # wait until all jobs were cancelled
        results = await asyncio.gather(*self._jobs.values(),
                                       return_exceptions=True)

        cancels = [res for res in results if
                   isinstance(res, asyncio.CancelledError)]

        logging.info("[queue] cancelled %d/%d job%s",
                     len(cancels),
                     to_cancel,
                     "s" if to_cancel > 1 else "")

    async def cancel_job(self, job):
        """ cancel the given job by accessing its future """

        if job not in self._jobs:
            logging.error("[queue] tried to cancel unknown job: %s", job)
        else:
            self._jobs[job].cancel()
