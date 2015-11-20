"""
Simulates the github pull request api.
"""

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import requests
import traceback

from tornado import web, gen
from tornado.platform.asyncio import AsyncIOMainLoop

from . import util
from . import service


class GitHub(service.Service):
    """
    The simlated github service. Uses config from kevin.
    """

    def __init__(self, args):
        super().__init__(args)

        self.status_handler = "/statusupdate"

    def run(self):
        """
        creates the interaction server
        """
        self.loop = asyncio.get_event_loop()
        AsyncIOMainLoop().install()

        print("Creating simulated server...")

        # create server
        self.app = web.Application([
            (self.status_handler, UpdateHandler, dict(config=self.cfg)),
        ])

        self.app.listen(self.port, address=str(self.listen))

        # submit web hook
        webhook = self.loop.create_task(self.submit_web_hook())

        try:
            self.loop.run_forever()
        except KeyboardInterrupt:
            print("exiting...")

        if not webhook.done():
            webhook.cancel()

        asyncio.wait(webhook, timeout=10)
        self.loop.close()

    @asyncio.coroutine
    def submit_web_hook(self):
        """
        create the webhook to kevin to trigger it.
        """

        if isinstance(self.listen, ipaddress.IPv6Address):
            ip = "[%s]" % (self.listen)

        status_url = "http://%s:%d%s" % (ip, self.port, self.status_handler)
        head_commit = yield from util.get_hash(self.repo)

        pull_req = {
            "pull_request": {
                "head": {
                    "repo": {
                        "clone_url": self.repo,
                    },
                    "sha": head_commit,
                },
                "statuses_url": status_url,
            },
        }

        payload = json.dumps(pull_req).encode()

        # calculate hmac
        signature = 'sha1=' + hmac.new(self.cfg.github_hooksecret,
                                       payload, hashlib.sha1).hexdigest()
        headers = {"X-Hub-Signature": signature}

        def submit_post():
            try:
                return requests.post(
                    url=self.cfg.dyn_url + "hook",
                    data=payload,
                    headers=headers,
                    timeout=5.0,
                )
            except requests.exceptions.RequestException as exc:
                print("failed delivering webhook: %s" % (exc))
                return "failed."

        post = self.loop.run_in_executor(None, submit_post)
        hook_answer = yield from post

        print("hook delivery: %s" % hook_answer)


class UpdateHandler(web.RequestHandler):
    """
    Handles a POST from kevin.
    """

    def initialize(self, config):
        self.cfg = config

    def get(self):
        self.write(b"Expected a JSON-formatted POST request.\n")
        self.set_status(400)
        self.finish()

    def post(self):
        print("\x1b[34mUpdate from %s\x1b[m" % self.request.remote_ip)
        blob = self.request.body
        try:
            auth_header = self.request.headers.get('Authorization').encode()
            if auth_header is None:
                self.set_status(401, "no authorization given!")
                self.finish()
                return

            if not auth_header.startswith(b"Basic "):
                raise ValueError("wrong auth type")

            auth = base64.decodebytes(auth_header[6:]).decode().split(":", 2)

            if tuple(auth) != self.cfg.github_authtok:
                print("wrong auth tried: %s" % (auth,))
                print("expected: %s" % (self.cfg.github_authtok,))
                raise ValueError("wrong authentication")

            self.handle_update(blob)

        except (ValueError, KeyError) as exc:
            print("bad request: " + repr(exc))
            traceback.print_exc()

            self.write(repr(exc).encode())
            self.set_status(400, "Bad request")

        except (BaseException) as exc:
            print("\x1b[31;1mexception in post hook\x1b[m")
            traceback.print_exc()

            self.set_status(500, "Internal error")
            self.set_header("Status", "internal fail")
        else:
            self.write(b"OK")
            self.set_header("status", "ok")
        self.finish()

    def handle_update(self, data):
        print("got update: %s" % data)
