from __future__ import annotations

import asyncio
import json
import logging
import typing
from urllib.parse import quote

import aiohttp

from .update import GitHubStatusURL, GitHubLabelUpdate
from ...config import CFG
from ...update import (BuildState, JobState, StepState)
from ...watcher import Watcher

if typing.TYPE_CHECKING:
    from typing import Any
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

    remembers all updates.
    remembers which github-status urls are known.
    on new url, send all previous updates to that new url.
    new updates are sent to all urls known.
    """
    def __init__(self, build: Build, config) -> None:
        self._status_update_urls: set[str] = set()

        self._cfg = config
        self._build = build

        # updates that we received.
        self._updates: list[Update] = list()

        # jsondata, url, method
        self._update_send_queue: asyncio.Queue[tuple[dict[str, Any], str, str]] = asyncio.Queue(maxsize=1000)

        self._sender_task = asyncio.get_event_loop().create_task(
            self._github_status_worker()
        )

    async def on_update(self, update):
        """
        Translates the update to a JSON GitHub status update request

        This method sends certain job updates to github,
        to allow near real-time status information.
        """

        if update is StopIteration:
            return

        if isinstance(update, GitHubStatusURL):
            if ("any" not in self._cfg.repos
                and update.repo_name not in self._cfg.repos):

                # this status url is not handled by this status updater.
                # => we don't have the auth token to send valid updates
                #    to the github API.
                return

            newurl = update.destination

            # we got some status update url
            self._status_update_urls.add(newurl)

            # send all previous updates to that url.
            for old_update in self._updates:
                await self._github_notify(old_update, url=newurl)

            return

        elif isinstance(update, GitHubLabelUpdate):
            if ("any" not in self._cfg.repos
                and update.repo_name not in self._cfg.repos):
                return

            if update.action == "remove":
                # DELETE /repos/:owner/:repo/issues/:issue_number/labels/:name
                url = f"{update.issue_url}/labels/{quote(update.label)}"
                await self._github_send_status(None, url, "delete")
            return

        # store the update so we can send it to a new client later
        # even if we don't have an url yet (it may arrive later)
        self._updates.append(update)

        # then actually notify github.
        await self._github_notify(update)

    async def _github_notify(self, update, url=None):
        """ prepare sending an update to github. """

        if not (url or self._status_update_urls):
            # no status update url known
            # we discard the update as we have nowhere to send it.
            # once somebody wants to known them, we stored it already.
            return

        # craft the update message
        if isinstance(update, BuildState):
            state, description = update.state, update.text
            context = CFG.ci_name
            target_url = None

        elif isinstance(update, JobState):
            state, description = update.state, update.text
            context = "%s: %s" % (CFG.ci_name,
                                  update.job_name)
            target_url = "%s&job=%s" % (
                self._build.target_url,
                update.job_name
            )

        elif isinstance(update, StepState):
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

        else:
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

        if not url:
            for destination in self._status_update_urls:
                await self._github_send_status(data, destination, "post")
        else:
            await self._github_send_status(data, url, "post")

    async def _github_send_status(self, data, url, req_method):
        """
        send a single github status update

        data: the payload
        url: http url
        req_method: http method, e.g. get, post, ...

        this request is put into a queue, where it is
        processed by the worker task.
        """

        try:
            self._update_send_queue.put_nowait((data, url, req_method))

        except asyncio.QueueFull:
            logging.error("[github] request queue full! wtf!?")

    async def _github_status_worker(self):
        """
        works through the update request queue
        """

        retries = 3
        retry_delay = 5

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

            if not delivered:
                logging.warning("[github] could not deliver %s request to %s "
                                "in %d tries with %s s retry delay, "
                                "skipping it...",
                                method, url, retries, retry_delay)

    async def _github_submit(self, data, url, req_method, timeout=10.0):
        """ send a request to the github API """
        try:
            authinfo = aiohttp.BasicAuth(self._cfg.auth_user,
                                         self._cfg.auth_pass)

            timeout = aiohttp.ClientTimeout(total=timeout)

            async with aiohttp.ClientSession(timeout=timeout,
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
