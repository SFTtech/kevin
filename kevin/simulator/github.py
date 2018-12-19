"""
Simulates the github pull request api.
"""

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import pathlib
import requests
import traceback

from tornado import web, gen
from tornado.platform.asyncio import AsyncIOMainLoop

from . import util
from . import service
from ..service.github import GitHubHook


class GitHub(service.Service):
    """
    The simlated github service. Uses config from kevin.
    """

    def __init__(self, args):
        super().__init__(args)

        self.statusurl = args.statusurl
        # url where to push updates to.
        self.status_handler = "/%s" % args.statuspath
        self.repo_handler = "/repo"

        self.pull_id = args.pull_id

    @classmethod
    def argparser(cls, subparsers):
        cli = subparsers.add_parser("github", help="simulate github")
        cli.add_argument("--statusurl", default=None,
                         help="public status url this simulator can be joined")
        cli.add_argument("--statuspath", default="statusupdate",
                         help="url component where status updates are sent")
        cli.add_argument("--pull-id", type=int, default=1337,
                         help="the pull request id number, e.g. 1337")
        cli.set_defaults(service=cls)

    def run(self):
        """
        creates the interaction server
        """
        self.loop = asyncio.get_event_loop()
        AsyncIOMainLoop().install()

        logging.info("Creating simulated server...")

        # create server
        handlers = [
            (self.status_handler, UpdateHandler, {"config": self.cfg,
                                                  "project": self.project}),
        ]

        # add http server to serve a local repo to qemu
        if self.local_repo and pathlib.Path(self.repo).is_dir():
            if not pathlib.Path(self.repo).joinpath("HEAD").is_file():
                logging.error("\x1b[33;1m%r doesn't look like a .git folder!\x1b[m" %
                      self.repo)

            logging.info("Serving '%s' on 'http://%s:%d%s/'" % (
                self.repo,
                self.listen,
                self.port,
                self.repo_handler
            ))

            handlers.append(
                (r"%s/(.*)" % self.repo_handler,
                 web.StaticFileHandler, dict(path=self.repo))
            )
            self.repo_vm = "http://%s:%d%s" % (self.local_repo_address,
                                               self.port,
                                               self.repo_handler)

        self.app = web.Application(handlers)
        logging.info("listening on port %s:%d" % (self.listen, self.port))
        self.app.listen(self.port, address=str(self.listen))

        # perform the request
        logging.info("add submit web hook task to the event loop")
        webhook = self.loop.create_task(self.request())

        logging.info("running the event loop...")
        try:
            self.loop.run_forever()
        except KeyboardInterrupt:
            logging.info("exiting...")

        if not webhook.done():
            webhook.cancel()

        self.loop.stop()
        self.loop.run_forever()
        self.loop.close()

    async def request(self):
        """
        Perform the requests to simulate this pull.
        """
        try:
            await self.submit_web_hook()

        except Exception:
            logging.exception("simulated pull request failed")

    async def submit_web_hook(self):
        """
        create the webhook to kevin to trigger it.
        """

        try:
            logging.info("submitting web hook...")
            if self.statusurl is None:
                logging.debug("submitting web hook... 1")
                if isinstance(self.listen, ipaddress.IPv6Address):
                    logging.debug("submitting web hook... 2")
                    ip = "[%s]" % (self.listen)
                    logging.debug("submitting web hook... 3")
                else:
                    logging.debug("submitting web hook... 4")
                    ip = str(self.listen)
                    logging.debug("submitting web hook... 5")

                status_url = "http://%s:%d%s" % (ip, self.port, self.status_handler)
                logging.debug("submitting web hook... 6")
            else:
                logging.debug("submitting web hook... 7")
                status_url = "%s%s" % (self.statusurl, self.status_handler)
                logging.debug("submitting web hook... 8")
        except:
            logging.exception("error:")

        logging.debug("wait for repo hash...")
        head_commit = await util.get_hash(self.repo)

        if self.repo_vm:
            logging.debug("wait for server info...")
            await util.update_server_info(self.repo)

        repo = self.repo_vm or self.repo
        project = self.cfg.projects[self.project]

        # chose first github trigger in the project config
        trigger = None
        for trigger_test in project.triggers:
            if isinstance(trigger_test, GitHubHook):
                trigger = trigger_test
                break

        if trigger is None:
            raise Exception("couldn't find github trigger in project.")

        # select first allowed repo as virtual "origin"
        reponame = trigger.repos[0]
        hooksecret = trigger.hooksecret

        pull_req = {
            "action": "synchronize",
            "sender": {"login": "rolf"},
            "number": self.pull_id,
            "pull_request": {
                "head": {
                    "repo": {
                        "clone_url": repo,
                    },
                    "sha": head_commit,
                    "label": "lol:epic_update",
                },
                "html_url": repo,
                "statuses_url": status_url,
            },
            "repository": {
                "full_name": reponame,
            },
        }

        payload = json.dumps(pull_req).encode()

        # calculate hmac
        signature = 'sha1=' + hmac.new(hooksecret,
                                       payload, hashlib.sha1).hexdigest()
        headers = {"X-Hub-Signature": signature,
                   "X-GitHub-Event": "pull_request"}

        def submit_post():
            try:
                return requests.post(
                    url="http://%s:%d/hook-github" % (
                        self.cfg.dyn_host,
                        self.cfg.dyn_port
                    ),
                    data=payload,
                    headers=headers,
                    timeout=5.0,
                )
            except requests.exceptions.RequestException as exc:
                logging.error("failed delivering webhook: %s" % (exc))
                return "failed."

        post = self.loop.run_in_executor(None, submit_post)
        logging.debug("wait for hook delivery...")
        hook_answer = await post

        logging.info("hook delivery: %s" % hook_answer)


class UpdateHandler(web.RequestHandler):
    """
    Handles a POST from kevin.
    """

    def initialize(self, config, project):
        self.cfg = config
        self.project = project

    def get(self):
        self.write(b"Expected a JSON-formatted POST request.\n")
        self.set_status(400)
        self.finish()

    def post(self):
        logging.info("\x1b[34mUpdate from %s:\x1b[m" % self.request.remote_ip)
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

            authcfg = self.cfg.projects[self.project].actions[0]
            authtok = (authcfg.auth_user, authcfg.auth_pass)

            if tuple(auth) != authtok:
                logging.error("wrong auth tried: %s" % (auth,))
                logging.error("expected: %s" % (authtok,))
                raise ValueError("wrong authentication")

            self.handle_update(blob)

        except (ValueError, KeyError) as exc:
            logging.exception("bad request: " + repr(exc))

            self.write(repr(exc).encode())
            self.set_status(400, "Bad request")

        except Exception as exc:
            logging.exception("\x1b[31;1mexception in post hook\x1b[m")

            self.set_status(500, "Internal error")
            self.set_header("Status", "internal fail")
        else:
            self.write(b"OK")
            self.set_header("status", "ok")
        self.finish()

    def handle_update(self, data):
        """
        Process a received update and present it in a shiny way graphically.
        Ensures maximum readability by dynamically formatting the text in
        a responsive way, that is even available on mobile devices.

        This output is plattform-independent, it may even work on windows.
        TODO: write testcases
        """
        logging.info("%s" % data)
