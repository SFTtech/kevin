"""
Job queuing for Kevin.
"""

import asyncio
import time

from .config import CFG


class Queue:
    """
    Job queue to manage pending builds and jobs.
    """

    def __init__(self):
        # all builds that are pending
        self.pending_builds = set()

        # build_id -> build
        self.build_ids = dict()

        # jobs that should be run
        self.job_queue = asyncio.Queue(maxsize=CFG.max_jobs_queued)

    def add_build(self, build):
        """
        Add a build to be processed.
        Called from where a new build was created and should now be run.
        """

        print("[queue] added build: [\x1b[33m%s\x1b[m] @ %s" % (
                build.commit_hash, build.clone_url))

        if build in self.pending_builds:
            return

        self.pending_builds.add(build)
        self.build_ids[build.commit_hash] = build

        # send signal to build so it can notify its jobs to add themselves!
        build.on_enqueue(self)

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

    def add_job(self, job):
        """ Add a job to the queue """

        if job.completed:
            # don't enqueue completed jobs.
            return

        try:
            # place the job into the pending list.
            self.job_queue.put_nowait(job)

        except asyncio.QueueFull:
            job.error("overloaded; job was dropped.")

    async def get_job(self):
        """ Return the next job to be processed """
        return await self.job_queue.get()


async def process_jobs(queue):
    """ process jobs from the queue forever """

    while True:
        print("[%s] \x1b[32mWaiting for job...\x1b[m" % (
            time.strftime("%Y-%m-%d %T")))

        # fetch new job from the queue
        current_job = await queue.get_job()

        print("[%s] \x1b[32mProcessing job...\x1b[m" % (
            time.strftime("%Y-%m-%d %T")))

        # TODO: for job parallelism, create the async task here:
        await current_job.run()

    new_job = await queue.get_job()
