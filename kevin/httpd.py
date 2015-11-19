"""
Web server to receive WebHook notifications from GitHub,
and provide them in a job queue.
"""

import json
from tornado import websocket, web, ioloop, queues, gen
from threading import Thread
import queue
import requests

from .config import CFG
from . import jobs
from .jobupdate import StdOut
from .service import github


class HTTPD(Thread):
    """
    This thread contains a server that listens for Github WebHook
    notifications to provide new jobs via the blocking get_job(),
    and offers job information via websocket and plain streams.

    TODO: service switch to support other than github.
    """
    def __init__(self):
        super().__init__()
        self.app = web.Application([
            ('/', PlainStreamHandler),
            ('/ws', WebSocketHandler),
            ('/hook', github.HookHandler)
        ])

        self.app.job_queue = queue.Queue(maxsize=CFG.max_jobs_queued)

        self.app.listen(CFG.dyn_port)

    def run(self):
        ioloop.IOLoop.instance().start()

    def stop(self):
        """ Cleanly stops the server, and joins the server thread. """
        ioloop.IOLoop.instance().stop()
        self.join()

    def get_job(self):
        """ Returns the next job from job_queue. """
        return self.app.job_queue.get()


class WebSocketHandler(websocket.WebSocketHandler):
    """ Provides a job description stream via WebSocket """
    def open(self):
        self.job = None

        job_id = self.request.query_arguments["job"][0]
        self.job = jobs.get_existing(job_id.decode())
        self.job.watch(self)

    def on_close(self):
        if self.job is not None:
            self.job.unwatch(self)

    def new_update(self, msg):
        """
        Called by the watched job when an update arrives.
        """
        if msg is StopIteration:
            self.close()
        else:
            self.write_message(msg.json())


class PlainStreamHandler(web.RequestHandler):
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
