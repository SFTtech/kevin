"""
Web server to receive WebHook notifications from GitHub,
and provide them in a job queue.
"""

from tornado import websocket, web, ioloop, queues, gen
from threading import Thread

from . import jobs
from .config import CFG
from .job import new_job
from .jobupdate import StdOut, BuildState
from .watcher import Watcher


class HTTPD(Thread):
    """
    This thread contains a server that listens for WebHook
    notifications to put jobs in the job_queue,
    and offers job information via websocket and plain streams.

    handlers: (url, handlercls) -> [cfg, cfg, cfg, ...]
    """
    def __init__(self, handlers, job_queue):
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

        # TODO: find a better location, same reason as below
        self.app.job_queue = job_queue

        # TODO: sanitize... dirty hack because tornado sucks.
        #       that way, a request handler can add jobs to the global queue
        def enqueue_job(job, timeout=0):
            try:
                # place the job into the pending list.
                job_queue.put(job, timeout)
            except queue.Full:
                job.error("overloaded; job was dropped.")

            job.update(BuildState("pending", "enqueued"))

        self.app.enqueue_job = enqueue_job

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
        self.job = None

        job_id = self.request.query_arguments["job"][0]
        self.job = jobs.get_existing(job_id.decode())
        self.job.watch(self)

    def on_close(self):
        if self.job is not None:
            self.job.unwatch(self)

    def on_message(self, message):
        # TODO: websocket protocol
        pass

    def new_update(self, msg):
        """
        Called by the watched job when an update arrives.
        """
        if msg is StopIteration:
            self.close()
        else:
            self.write_message(msg.json())


class PlainStreamHandler(web.RequestHandler, Watcher):
    """ Provides the job stdout stream via plain HTTP GET """
    @gen.coroutine
    def get(self):
        print("plain opened")
        self.job = None

        try:
            job_id = self.request.query_arguments["job"][0]
        except (KeyError, IndexError):
            self.write(b"no job id given\n")
            return

        job_id = job_id.decode(errors='replace')
        try:
            self.job = jobs.get_existing(job_id)
        except ValueError:
            self.write(("no such job: " + repr(job_id) + "\n").encode())
            return
        else:
            self.queue = queues.Queue()
            self.job.watch(self)

        while True:
            update = yield self.queue.get()
            if update is StopIteration:
                return
            if isinstance(update, StdOut):
                self.write(update.data.encode())
                self.flush()

        self.finish()

    def new_update(self, msg):
        """ Pur a message to the stream queue """
        self.queue.put(msg)

    def on_connection_close(self):
        """ add a connection-end marker to the queue """
        self.new_update(StopIteration)

    def on_finish(self):
        print("plain closed")
        if self.job is not None:
            self.job.unwatch(self)
