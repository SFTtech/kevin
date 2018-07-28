"""
Job queuing for Kevin.
"""

import asyncio
import functools
import logging


class Queue:
    """
    Job queue to manage pending builds and jobs.
    """

    def __init__(self, loop, job_manager, max_running=1, max_queued=30):
        # event loop
        self.loop = loop

        # job distribution
        self.job_manager = job_manager

        # all builds that are pending
        self.pending_builds = set()

        # build_id -> build
        self.build_ids = dict()

        # jobs that should be run
        self.job_queue = asyncio.Queue(maxsize=max_queued)

        # running job futures
        # job -> job_future
        self.jobs = dict()

        # was the execution of the queue cancelled
        self.cancelled = False

        # number of jobs running in parallel
        self.max_running = max_running

        # keep processing jobs
        self.processing = loop.create_task(self.process_jobs())

    async def add_build(self, build):
        """
        Add a build to be processed.
        Called from where a new build was created and should now be run.
        """

        if build in self.pending_builds:
            return

        if build.requires_run():
            logging.info("[queue] adding build: [\x1b[33m%s\x1b[m] @ %s",
                         build.commit_hash,
                         build.clone_url)

            self.pending_builds.add(build)
            self.build_ids[build.commit_hash] = build

            # the build shall now run.
            # this is done by adding jobs to this queue.
            await build.run(self)

    def remove_build(self, build):
        """ Remove a finished build """
        del self.build_ids[build.commit_hash]
        self.pending_builds.remove(build)

    def abort_build(self, build_id):
        """ Abort a running build by aborting all pending jobs """

        build = self.build_ids.get(build_id)

        if build:
            if not build.completed:
                build.abort()

    def is_pending(self, commit_hash):
        """ Test if a commit hash is currently being built """
        # TODO: what if a second project wants the same hash?
        #       we can't reuse the build then!
        return commit_hash in self.build_ids.keys()

    async def add_job(self, job):
        """ Add a job to the queue """

        if job.completed:
            # don't enqueue completed jobs.
            return

        try:
            # place the job into the pending list.
            self.job_queue.put_nowait(job)

        except asyncio.QueueFull:
            await job.error("overloaded; job was dropped.")

    async def process_jobs(self):
        """ process jobs from the queue forever """

        while not self.cancelled:

            if self.job_queue.empty():
                logging.info("[queue] \x1b[32mWaiting for job...\x1b[m")

            # fetch new job from the queue
            job = await self.job_queue.get()

            logging.info("[queue] \x1b[32mProcessing job\x1b[m %s.%s for "
                         "[\x1b[34m%s\x1b[m]...",
                         job.build.project.name,
                         job.name,
                         job.build.commit_hash)

            # create the run.
            # the job will be distributed to one of the runners.
            job_fut = self.loop.create_task(job.run(self.job_manager))

            self.jobs[job] = job_fut

            # register the callback when the job is done
            job_fut.add_done_callback(functools.partial(
                self.job_done, job=job))

            # wait for jobs to complete if there are too many running
            # this can be done very dynamically in the future.
            if len(self.jobs) >= self.max_running or self.cancelled:
                logging.warning("[queue] runlimit of %d reached, "
                                "waiting for completion...", self.max_running)

                # wait until a "slot" is available, then the next job
                # can be processed.
                await asyncio.wait(
                    self.jobs.values(),
                    return_when=asyncio.FIRST_COMPLETED)

    def job_done(self, task, job):
        """ callback for finished jobs """
        del task  # unused

        logging.info("[queue] Job %s.%s finished for [\x1b[34m%s\x1b[m].",
                     job.build.project.name,
                     job.name,
                     job.build.commit_hash)

        try:
            del self.jobs[job]
        except KeyError:
            logging.error("\x1b[31mBUG\x1b[m: job %s not in running set", job)

    async def cancel(self):
        """ cancel all running jobs """

        to_cancel = len(self.jobs)
        self.cancelled = True

        if to_cancel == 0:
            return

        logging.info("[queue] cancelling running jobs...")

        for job_fut in self.jobs.values():
            job_fut.cancel()

        # wait until all jobs were cancelled
        results = await asyncio.gather(*self.jobs.values(),
                                       return_exceptions=True)

        cancels = [res for res in results if
                   isinstance(res, asyncio.CancelledError)]

        logging.info("[queue] cancelled %d/%d job%s",
                     len(cancels),
                     to_cancel,
                     "s" if to_cancel > 1 else "")

    def cancel_job(self, job):
        """ cancel the given job by accessing its future """

        if job not in self.jobs:
            logging.error("[queue] tried to cancel unknown job: %s", job)
        else:
            self.jobs[job].cancel()

    async def shutdown(self):
        """
        cancel all jobs that are pending and shutdown the queue
        """
        if not self.processing.done():
            await self.cancel()

            # cancel the job processing
            self.processing.cancel()
            try:
                await self.processing
            except asyncio.CancelledError:
                # expected :)
                logging.debug("[queue] processing canceled.")

        else:
            logging.warning("[queue] queue processing was already done!")
