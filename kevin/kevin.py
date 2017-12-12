"""
Main Queue and Falk management entity.
"""

from .build_manager import BuildManager
from .httpd import HTTPD
from .job_manager import JobManager
from .jobqueue import Queue


class Kevin:
    """
    This is Kevin. He will build your job. Guaranteed to be bug-free(*).

    Jobs from various sources are put into the Queue,
    which is processed by running each jobs via Falk with Chantal.

    (*) Disclaimer: May not actually be bug-free.
    """

    def __init__(self, loop, config):
        self.loop = loop

        # TODO: load "plugins"
        # loader = importlib.machinery.SourceFileLoader(m, 'lol/' + m + '.py')
        # b.register(loader.load_module(m))

        # job distribution
        self.job_manager = JobManager(loop, config)

        # build creation
        self.build_manager = BuildManager()

        # queue where build jobs will end up in
        self.queue = Queue(loop, self.job_manager,
                           config.max_jobs_running, config.max_jobs_queued)

        # webserver: receives hooks and provides websocket api
        self.httpd = HTTPD(config.urlhandlers, self.queue, self.build_manager)

    def run(self):
        """
        Run Kevin foreveeeeeerrrrrrrrrr!
        """

        self.loop.run_forever()

    async def shutdown(self):
        """
        Cancel running jobs and stop processing new tasks.
        """

        # stop http listening
        await self.httpd.stop()

        # terminate falk connections
        await self.job_manager.shutdown()

        # terminate the job waiting queue
        await self.queue.shutdown()
