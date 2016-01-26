"""
Web server to receive WebHook notifications from GitHub,
and provide them in a job queue.
"""

from threading import Thread

from tornado import websocket, web, ioloop, gen
from tornado.queues import Queue

from .build import get_build
from .config import CFG
from .update import StdOut, BuildState, JobState
from .watcher import Watcher


class HTTPD(Thread):
    """
    This thread contains a server that listens for WebHook
    notifications to spawn triggered actions, e.g. new Builds.
    It also provides the websocket API and plain log streams for curl.

    handlers: (url, handlercls) -> [cfg, cfg, ...]
    """
    def __init__(self, handlers, queue):
        super().__init__()

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

        # TODO: sanitize... dirty hack because tornado sucks.
        #       that way, a request handler can add jobs to the queue
        self.app.queue = queue

        # bind to tcp port
        self.app.listen(CFG.dyn_port)

    def run(self):
        ioloop.IOLoop.instance().start()

    def stop(self):
        """ Cleanly stops the server, and joins the server thread. """
        ioloop.IOLoop.instance().stop()
        self.join()


class WebSocketHandler(websocket.WebSocketHandler, Watcher):
    """ Provides a job description stream via WebSocket """
    def open(self):
        self.build = None

        project = self.request.query_arguments["project"][0].decode()
        build_id = self.request.query_arguments["hash"][0].decode()
        self.build = get_build(project, build_id)

        if not self.build:
            self.write_message("no such build")
            return
        else:
            self.build.watch(self)

    def on_close(self):
        if self.build is not None:
            self.build.unwatch(self)

    def on_message(self, message):
        # TODO: websocket protocol
        pass

    def on_update(self, msg):
        """
        Called by the watched build when an update arrives.
        """
        if msg is StopIteration:
            self.close()
        else:
            self.write_message(msg.json())


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
            self.job.watch(self)

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
        if self.job is not None:
            self.job.unwatch(self)
