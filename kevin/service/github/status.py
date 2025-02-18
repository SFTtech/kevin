from __future__ import annotations

import asyncio
import json
import logging
import typing
from urllib.parse import quote

import aiohttp

from .update import GitHubStatusURL, GitHubLabelUpdate
from ...config import CFG
from ...update import (BuildFinished, BuildState, JobState, StepState)
from ...watcher import Watcher

if typing.TYPE_CHECKING:
    from .action import GitHubStatus
    from ...update import Update
    from ...build import Build


# translation lookup-table for kevin states -> github states
STATE_TRANSLATION = {
    "waiting": "pending",
    "running": "pending",
    "success": "success",
    "failure": "failure",
    "error": "error",
    "skipped": "failure"
}


class GitHubBuildStatusUpdater(Watcher):
    """
    Sets the GitHub build and job/step statuses from job updates.

    This is constructed for each build to update.

    For each GitHub status url (where github accepts messages for displaying to users),
    launches a GitHubStatuSender.
    """
    def __init__(self, build: Build, config: GitHubStatus) -> None:
        self._cfg = config
        self._build = build

        # target_id -> status sender
        self._senders: dict[str, GitHubStatusSender] = dict()

    async def on_update(self, update: Update):
        """
        Translates the update to a JSON GitHub status update request

        This method sends certain job updates to github,
        to allow near real-time status information.
        """

        match update:
            case GitHubStatusURL():
                if ("any" not in self._cfg.repos
                    and update.repo not in self._cfg.repos):

                    # this status url is not handled by this status updater.
                    # => we don't have the auth token to send valid updates
                    #    to the github API.
                    return

                update_target = update.target_id()
                if update_target in self._senders:
                    # we already send there
                    return

                status_sender = GitHubStatusSender(self._cfg, self._build, update.destination)
                self._senders[update_target] = status_sender
                await self._build.register_watcher(status_sender)


class GitHubStatusSender(Watcher):
    def __init__(self, config: GitHubStatus, build: Build, url: str) -> None:
        self._cfg = config
        self._build = build
        self._destination = url

        # jsondatastr, url, method
        self._update_send_queue: asyncio.Queue[tuple[str | None, str, str]] = asyncio.Queue(maxsize=1000)

        self._sender_task = asyncio.get_event_loop().create_task(
            self._github_status_worker()
        )

    async def on_update(self, update: Update) -> None:
        match update:
            case GitHubLabelUpdate():
                if ("any" not in self._cfg.repos
                    and update.repo not in self._cfg.repos):
                    return

                if update.action == "remove":
                    # DELETE /repos/:owner/:repo/issues/:issue_number/labels/:name
                    url = f"{update.issue_url}/labels/{quote(update.label)}"
                    await self._github_send_status(None, "delete", custom_url=url)
                return

            case BuildFinished():
                self._build.deregister_watcher(self)

        # then actually notify github.
        await self._github_notify(update)

    async def _github_notify(self, update: Update) -> None:
        """ prepare sending an update to github. """

        # craft the update message
        match update:
            case BuildState():
                state, description = update.state, update.text
                context = CFG.ci_name
                target_url = None

            case JobState():
                state, description = update.state, update.text
                context = "%s: %s" % (CFG.ci_name,
                                    update.job_name)
                target_url = "%s&job=%s" % (
                    self._build.target_url,
                    update.job_name
                )

            case StepState():
                state, description = update.state, update.text
                context = "%s: %s-%02d %s" % (CFG.ci_name,
                                              update.job_name,
                                              update.step_number,
                                              update.step_name)

                target_url = "%s&job=%s#%s" % (
                    self._build.target_url,
                    update.job_name,
                    update.step_name
                )

            case _:
                # unhandled update
                return

        if len(description) > 140:
            logging.warning("[github] update description too long, truncating")
            description = description[:140]

        data = json.dumps({
            "context": context,
            "state": STATE_TRANSLATION[state],
            "description": description,
            "target_url": target_url
        })

        await self._github_send_status(data, "post")

    async def _github_send_status(self, data: str | None, req_method: str, custom_url: str | None = None) -> None:
        """
        send a single github status update

        data: the payload
        req_method: http method, e.g. get, post, ...
        custom_url: http url if needed

        this request is put into a queue, where it is
        processed by the worker task.
        """

        # have a custom_url so the GitHubLabelUpdate can enqueue the label modification
        url = custom_url or self._destination

        try:
            self._update_send_queue.put_nowait((data, url, req_method))

        except asyncio.QueueFull:
            logging.error("[github] request queue full! wtf!?")

    async def _github_status_worker(self) -> None:
        """
        works through the update request queue
        """

        retries = 5       # max number of attempts
        retry_delay = 5   # start delay
        retry_factor = 3  # multiply factor for each further attempt

        while True:
            data, url, method = await self._update_send_queue.get()

            delivered = False
            for _ in range(retries):
                try:
                    delivered = await self._github_submit(data, url, method)
                    if delivered:
                        break
                except Exception:
                    logging.exception("[github] exception occured when sending"
                                      f" {method} API request to {url!r}")

                await asyncio.sleep(retry_delay)
                retry_delay *= retry_factor

            if not delivered:
                logging.warning("[github] could not deliver %s request to %s "
                                "in %d tries with %s s retry delay, "
                                "skipping it...",
                                method, url, retries, retry_delay)

    async def _github_submit(self, data: str | None, url: str,
                             req_method: str, timeout: float = 10.0) -> bool:
        """ send a request to the github API """
        try:
            authinfo = aiohttp.BasicAuth(self._cfg.auth_user,
                                         self._cfg.auth_pass)

            _timeout = aiohttp.ClientTimeout(total=timeout)

            async with aiohttp.ClientSession(timeout=_timeout,
                                             trust_env=True) as session:
                assert req_method in {"post", "get", "delete", "put", "patch"}

                async with session.request(req_method.upper(), url,
                                           data=data, auth=authinfo) as reply:
                    if 200 <= reply.status < 300:
                        logging.debug("[github] update delivered successfully:"
                                      " %s", reply.status)
                        return True

                    elif "status" in reply.headers:
                        logging.warning("[github] status update request to %s "
                                        "rejected by github (%d): %s\n%s",
                                        url,
                                        reply.status,
                                        reply.headers["status"],
                                        await reply.text())
                        return False

                    logging.warning("[github] update to %s failed with"
                                    "http code %d, "
                                    "and no status report",
                                    url,
                                    reply.status)
                    return False

        except aiohttp.ClientConnectionError as exc:
            logging.warning("[github] Failed status connection to '%s': %s",
                            url, exc)
            return False
