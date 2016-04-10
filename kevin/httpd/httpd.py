"""
Web server to receive WebHook notifications from GitHub,
and provide them in a job queue.
"""

from abc import ABCMeta, abstractmethod

from tornado import websocket, web, ioloop, gen
from tornado.platform.asyncio import AsyncIOMainLoop
from tornado.queues import Queue

from ..build import get_build
from ..config import CFG
from ..update import StdOut, JobState
from ..watcher import Watcher

from .websocket import WebSocketHandler


class HTTPD:
    """
    This class contains a server that listens for WebHook
    notifications to spawn triggered actions, e.g. new Builds.
    It also provides the websocket API and plain log streams for curl.

    handlers: (url, handlercls) -> [cfg, cfg, ...]
    queue: the jobqueue.Queue where new builds/jobs are put in
    """
    def __init__(self, handlers, queue):
        # use the main asyncio loop to run tornado
        AsyncIOMainLoop().install()

        urlhandlers = dict()
        urlhandlers[("/", PlainStreamHandler)] = None
        urlhandlers[("/ws", WebSocketHandler)] = None

        urlhandlers.update(handlers)

        # create the tornado application
        # that serves assigned urls to handlers.
        handlers = list()
        for (url, handler), cfgs in urlhandlers.items():
            if cfgs is not None:
                handlers.append((url, handler, cfgs))
            else:
                handlers.append((url, handler))

        self.app = web.Application(handlers)

        self.app.queue = queue

        # bind to tcp port
        self.app.listen(CFG.dyn_port, address=str(CFG.dyn_address))


class PlainStreamHandler(web.RequestHandler, Watcher):
    """ Provides the job stdout stream via plain HTTP GET """
    @gen.coroutine
    def get(self):
        self.job = None

        try:
            project_name = self.request.query_arguments["project"][0]
        except (KeyError, IndexError):
            self.write(b"no project given\n")
            return

        try:
            build_id = self.request.query_arguments["hash"][0]
        except (KeyError, IndexError):
            self.write(b"no build hash given\n")
            return

        try:
            job_name = self.request.query_arguments["job"][0]
        except (KeyError, IndexError):
            self.write(b"no job given\n")
            return

        project_name = project_name.decode(errors='replace')
        build_id = build_id.decode(errors='replace')
        job_name = job_name.decode(errors='replace')

        try:
            project = CFG.projects[project_name]

        except KeyError:
            self.write(b"unknown project requested\n")
            return

        build = get_build(project, build_id)
        if not build:
            self.write(("no such build: project %s [%s]\n" % (
                project_name, build_id)).encode())
            return
        else:
            self.job = build.jobs.get(job_name)
            if not self.job:
                self.write(("unknown job in project %s [%s]: %s\n" % (
                    project_name, build_id, job_name)).encode())
                return

            # the message queue to be sent to the http client
            self.queue = Queue()

            # request the updates from the watched jobs
            self.job.send_updates_to(self)

            # emit the updates and wait until no more are coming
            yield self.watch_job()

    @gen.coroutine
    def watch_job(self):
        """ Process updates and send them to the client """

        self.set_header("Content-Type", "text/plain")

        while True:
            update = yield self.queue.get()

            if update is StopIteration:
                break

            if isinstance(update, StdOut):
                self.write(update.data.encode())

            elif isinstance(update, JobState):
                if update.is_errored():
                    self.write(
                        ("\x1b[31merror:\x1b[m %s\n" %
                         (update.text)).encode()
                    )
                elif update.is_succeeded():
                    self.write(
                        ("\x1b[32msuccess:\x1b[m %s\n" %
                         (update.text)).encode()
                    )
                elif update.is_finished():
                    self.write(
                        ("\x1b[31mfailed:\x1b[m %s\n" %
                         (update.text)).encode()
                    )

            yield self.flush()

        return self.finish()

    def on_update(self, update):
        """ Put a message to the stream queue """
        self.queue.put(update)

    def on_connection_close(self):
        """ Add a connection-end marker to the queue """
        self.on_update(StopIteration)

    def on_finish(self):
        # TODO: only do this if we got a GET request.
        if self.job is not None:
            self.job.stop_sending_updates_to(self)
