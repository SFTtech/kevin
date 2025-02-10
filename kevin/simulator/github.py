"""
Simulates the github pull request api.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import pathlib
import traceback
import typing

from aiohttp import web, ClientSession
from aiohttp.web import Request, Response

from . import util
from . import service
from ..service.github import GitHubHook

if typing.TYPE_CHECKING:
    from typing import Any


class GitHub(service.Service):
    """
    The simlated github service. Uses config from kevin.
    """

    @classmethod
    def argparser(cls, subparsers):
        cli = subparsers.add_parser("github", help="simulate github")
        cli.set_defaults(service=cls)
        cli.add_argument("--statuspath", default="statusupdate",
                         help="url component where status updates are sent")
        cli.add_argument("--user", default="H4ck3r",
                         help="GitHub user that submitted the repo update")

        sp = cli.add_subparsers(dest="action", required=True)
        pull_cli = sp.add_parser("pull_request")
        pull_cli.add_argument("--pull-id", type=int, default=1337,
                              help="the pull request id number, e.g. 1337")

        _push_cli = sp.add_parser("push")

    def __init__(self, args):
        super().__init__(args)

        # url where to push updates to.
        self._status_path = "/%s" % args.statuspath
        self._repo_path = "/repo"

        self._user = args.user

        self._action = args.action
        match args.action:
            case "pull_request":
                self._pull_id = args.pull_id
            case "push":
                pass
            case _:
                raise ValueError(f"unknown action {args.action}")

    async def run(self) -> None:
        """
        creates the interaction server
        """
        print("Creating simulated GitHub server...")

        app = web.Application()

        app.add_routes([web.post(self._status_path, self._handle_status_msg)])

        # add http server to serve a local repo to qemu
        if self.local_repo and pathlib.Path(self.repo).is_dir():
            if not pathlib.Path(self.repo).joinpath("HEAD").is_file():
                print(f"\x1b[33;1m{self.repo!r} doesn't look like a .git folder!\x1b[m")

            print(f"serving '{self.repo}' on 'http://{self.listen}:{self.port}{self._repo_path}/'")

            app.add_routes([web.static(self._repo_path, self.repo, show_index=True)])

            self.repo_server = f"http://{self.local_repo_address}:{self.port}{self._repo_path}"

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, str(self.listen), self.port)
        print("listening on port %s:%d" % (self.listen, self.port))
        await site.start()

        # perform the "pull request" trigger
        await self._submit_webhook()

        try:
            # run forever
            await asyncio.Future()
        except asyncio.CancelledError:
            await runner.cleanup()
            raise

    async def _submit_webhook(self):
        """
        create the webhook to kevin to trigger it.
        """

        if isinstance(self.listen, ipaddress.IPv6Address):
            ip = f"[{self.listen}]"
        else:
            ip = str(self.listen)

        status_url = "http://%s:%d%s" % (ip, self.port, self._status_path)

        branch_commit = await util.get_hash(self.repo, self.branch)
        branch_names = await util.get_refnames(self.repo, branch_commit, only_branches=True)
        if self.branch:
            branch_name = self.branch  # take the one we know anyway
        elif branch_names:
            branch_name = branch_names[0]  # for simplicity just take the first that matches...
        else:
            branch_name = "HEAD"

        if self.repo_server:
            await util.update_server_info(self.repo)

        repo = self.repo_server or self.repo
        project = self.cfg.projects[self.project]

        # chose first github trigger in the project config
        trigger = None
        for trigger_test in project.triggers:
            if isinstance(trigger_test, GitHubHook):
                trigger = trigger_test
                break

        if trigger is None:
            raise Exception("couldn't find github trigger in project.")

        # select one of the allowed repos as virtual "origin"
        reponame = trigger.repos.pop()
        hooksecret = trigger.hooksecret

        match self._action:
            case "pull_request":
                payload = _payload_pull_request(self._user, repo, reponame, branch_name, branch_commit, status_url, self._pull_id)
                event_type = "pull_request"
            case "push":
                payload = _payload_push(self._user, repo, reponame, branch_name, branch_commit, status_url)
                event_type = "push"
            case _:
                raise ValueError(f"unknown action {self._action}")

        payload_raw = json.dumps(payload).encode()

        # calculate hmac
        signature = 'sha1=' + hmac.new(hooksecret, payload_raw, hashlib.sha1).hexdigest()
        headers = {"X-Hub-Signature": signature,
                   "X-GitHub-Event": event_type}

        target_url = f"http://{self.cfg.dyn_frontend_host}:{self.cfg.dyn_frontend_port}/hooks/github"
        print(f"submitting pullreq webhook to {target_url!r}...")
        async with ClientSession() as session:
            async with session.post(target_url,
                                    data=payload_raw, headers=headers) as resp:
                print(f"hook answer {'ok' if resp.ok else 'bad'}: {await resp.text()!r}")

    async def _handle_status_msg(self, request: Request) -> Response:
        print(f"\x1b[34mUpdate from {request.remote}:\x1b[m", end=" ")
        blob = await request.text()
        try:
            auth_header = request.headers.get('Authorization')
            if auth_header is None:
                return Response(text="no authorization given!", status=401)

            if not auth_header.startswith("Basic "):
                raise ValueError("wrong auth type")

            auth = base64.b64decode(auth_header[6:]).decode().split(":", 2)

            authcfg = self.cfg.projects[self.project].actions[0]
            authtok = (authcfg.auth_user, authcfg.auth_pass)

            if tuple(auth) != authtok:
                print("wrong auth tried: %s" % (auth,))
                print("expected: %s" % (authtok,))
                raise ValueError("wrong authentication")

            self._show_update(blob)

        except (ValueError, KeyError) as exc:
            traceback.print_exc()

            return Response(text=f"{exc}", status=400)

        except Exception:
            print("\x1b[31;1mexception in post hook\x1b[m")
            traceback.print_exc()

            return Response(text="internal exception", status=500)

        return Response(text="ok")

    def _show_update(self, data):
        """
        Process a received update and present it in a shiny way graphically.
        Ensures maximum readability by dynamically formatting the text in
        a responsive way, that is even available on mobile devices.

        This output is plattform-independent, it may even work on windows.
        TODO: write testcases
        """
        print(data)


def _payload_pull_request(user, repo, reponame, branch_name, commit_hash, status_url, pull_id) -> dict[str, Any]:
    # most basic webhook for a pull request
    return {
        "action": "synchronize",
        "sender": {"login": user},
        "number": pull_id,
        "pull_request": {
            "head": {
                "repo": {
                    "clone_url": repo,
                },
                "sha": commit_hash,
                "ref": branch_name,
            },
            "html_url": repo,
            "statuses_url": status_url,
            "issue_url": f"{repo}/issues/1",
            "labels": [
                {
                    "name": "mylabel",
                    "value": "my awesome label",
                },
            ],
        },
        "repository": {
            "full_name": reponame,
        },
    }


def _payload_push(user, repo, reponame, branch_name, commit_hash, status_url) -> dict[str, Any]:
    return {
        "ref": f"refs/heads/{branch_name}",
        "repository": {
            "full_name": reponame,
            "clone_url": repo,
            "html_url": repo,
            "statuses_url": status_url,
        },
        "pusher": {
            "name": user,
        },
        "head_commit": {
            "id": commit_hash,
        }
    }
