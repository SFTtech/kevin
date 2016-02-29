"""
Job queuing for Kevin.
"""

import queue

from .config import CFG


class Queue:
    """
    Job queue to manage pending builds and jobs.
    """

    def __init__(self):
        self.pending_builds = set()
        self.build_ids = dict()
        self.job_queue = queue.Queue(maxsize=CFG.max_jobs_queued)

    def add_build(self, build):
        """
        Add a build to be processed.
        Called from where a new build was created and should now be run.
        """

        if build in self.pending_builds:
            print("[queue] known build: \x1b[2m[%s]\x1b[m @ %s" % (
                build.commit_hash, build.clone_url))
            return

        print("[queue] enqueueing build: \x1b[2m[%s]\x1b[m @ %s" % (
            build.commit_hash, build.clone_url))

        self.pending_builds.add(build)
        self.build_ids[build.commit_hash] = build

        # send signal to build so it can notify its jobs to add themselves!
        build.on_enqueue(self)

    def remove_build(self, build):
        """ Remove a finished build """
        print("[queue] removing build: \x1b[2m[%s]\x1b[m @ %s" % (
            build.commit_hash, build.clone_url))

        del self.build_ids[build.commit_hash]
        self.pending_builds.remove(build)

    def abort_build(self, build_id):
        """ Abort a running build by aborting all pending jobs """
        raise NotImplementedError()

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
            self.job_queue.put(job, timeout=0)

        except queue.Full:
            job.error("overloaded; job was dropped.")

    def get_job(self):
        """ Return the next job to be processed """
        return self.job_queue.get()
