"""
Web server to receive WebHook notifications from GitHub,
and provide them in a job queue.
"""

import json
from tornado import websocket, web, ioloop, queues, gen
from threading import Thread
import hmac
from hashlib import sha1
import queue
import requests

from .config import CFG
from . import jobs
from .jobupdate import StdOut, JobSource, BuildState, StepState


class HTTPD(Thread):
    """
    This thread contains a server that listens for Github WebHook
    notifications to provide new jobs via the blocking get_job(),
    and offers job information via websocket and plain streams.
    """
    def __init__(self):
        super().__init__()
        self.app = web.Application([
            ('/', PlainStreamHandler),
            ('/ws', WebSocketHandler),
            ('/hook', HookHandler)
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
        print("ws opened")
        self.job = None

        job_id = self.request.query_arguments["job"][0]
        self.job = jobs.get_existing(job_id.decode())
        self.job.watch(self)

    def on_close(self):
        print("ws closed")
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


class HookHandler(web.RequestHandler):
    """ Listens for GitHub WebHook POST requests """
    def get(self):
        self.write(b"Expected a JSON-formatted POST request.\n")
        self.set_status(400)

    def post(self):
        print("hook")
        blob = self.request.body
        try:
            signature = hmac.new(CFG.github_hooksecret, blob, sha1)
            signature = 'sha1=' + signature.hexdigest()
            if self.request.headers["X-Hub-Signature"] != signature:
                raise ValueError(
                    "signature invalid",
                    self.request.headers["X-Hub-Signature"],
                    signature
                )
            self.handle_json_blob(blob)
        except (ValueError, KeyError) as exc:
            print("bad request: " + repr(exc))
            self.write(repr(exc).encode())
            self.set_status(400, "Bad request")
        else:
            self.write(b"OK")

    def handle_json_blob(self, blob):
        """
        Called for each JSON-formatted POST request.
        blob is a bytes object.
        If the JSON blob describes a valid build job,
        a job object is instantiated and appended to server.job_queue.
        """
        json_data = json.loads(blob.decode())

        clone_url = json_data["pull_request"]["head"]["repo"]["clone_url"]
        commit_sha = json_data["pull_request"]["head"]["sha"]
        statuses_update_url = json_data["pull_request"]["statuses_url"]

        # brand_new says whether the job has not prevoiusly existed
        # and should be enqueued.
        # TODO: implement brand-new-detection
        job, brand_new = jobs.get(commit_sha, True)

        job.update(JobSource(
            clone_url=clone_url,
            repo_url=None,  # TODO get those from json_data
            author=None,
            branch=None,
            comment=None
        ))

        # install the github job state updater callback
        job.watch(
            GitHubBuildStatusUpdater(
                statuses_update_url,
                CFG.github_authtok,
                job.target_url
            )
        )

        if brand_new:
            print("enqueueing job: \x1b[2m[%s]\x1b[m @ %s" % (job.job_id,
                                                              job.clone_url))
            print("\x1b[1mcurl -N %s?job=%s\x1b[m" % (CFG.dyn_url, job.job_id))

            try:
                self.application.job_queue.put(job, timeout=0)
                job.update(BuildState("pending", "waiting for VM"))
            except queue.Full:
                job.error("overloaded; job was dropped.")


class GitHubBuildStatusUpdater:
    """
    Sets the GitHub build/build step statuses from job updates.

    The constructor takes the
     - statuses update url
     - auth token
     - job info url
    """
    def __init__(self, statuses_update_url, authtok, job_info_url):
        if not statuses_update_url.startswith('https://api.github.com/'):
            raise ValueError("bad statuses URL: " + statuses_update_url)

        self.statuses_update_url = statuses_update_url
        self.authtok = authtok
        self.job_info_url = job_info_url

    def new_update(self, update):
        """ Translates the update to a JSON GitHub status update request """
        if isinstance(update, BuildState):
            state, description = update.state, update.text
            context = CFG.ci_name
            target_url = self.job_info_url
        elif isinstance(update, StepState):
            state, description = update.state, update.text
            context = "%s: %02d %s" % (CFG.ci_name,
                                       update.step_number,
                                       update.step_name)
            target_url = self.job_info_url + "&step=" + update.step_name
        else:
            return

        if len(description) > 140:
            print("description too long for github, truncating")
            description = description[:140]

        data = json.dumps(dict(
            context=context,
            state=state,
            description=description,
            target_url=target_url
        ))
        reply = requests.post(self.statuses_update_url, data,
                              auth=self.authtok)
        if not reply.ok:
            replytext = reply.headers['Status'] + '\n' + reply.text
            print("status update request rejected by github: " + replytext)
