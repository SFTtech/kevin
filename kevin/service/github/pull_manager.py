"""
Cross-links between pull requests
"""
from __future__ import annotations

import logging
import typing

from .update import GitHubPullRequest
from ...update import (BuildState, QueueActions)
from ...watcher import Watcher

if typing.TYPE_CHECKING:
    from ...task_queue import TaskQueue
    from ...update import Update


class GitHubPullManager(Watcher):
    """
    Tracks running pull requests and aborts a running build
    if the same pull request gets an update.

    Subscribes to all builds.
    """

    def __init__(self, repos: set[str]) -> None:
        # repos this pullmanager is responsible for
        self.repos = repos

        # all the pulls that we triggered
        # (project_name, repo, pull_id) -> (commit_hash, queue)
        self._running_pull_builds: dict[tuple[str, str, int], tuple[str, TaskQueue | None]] = dict()

    async def on_update(self, update: Update):
        match update:
            case GitHubPullRequest():
                # new pull request information that may cause an abort.
                key = (update.project_name, update.repo, update.pull_id)

                if update.repo not in self.repos:
                    # repo is not handled by this pull manager,
                    # don't do anything.
                    return

                # get the running build id for this pull request
                entry = self._running_pull_builds.get(key)

                if entry is not None:
                    commit_hash, queue = entry

                else:
                    # that pull is not running currently, so
                    # store that it's running.
                    # the queue is unknown, set it to None.
                    self._running_pull_builds[key] = (update.commit_hash, None)
                    return

                if commit_hash == update.commit_hash:
                    # the same build is running currently, just ignore it
                    pass

                else:
                    # the pull request is running already,
                    # now abort the previous build for it.

                    if not queue:
                        # we didn't get the "Enqueued" update for the build
                        logging.warning("[github] wanted to abort build "
                                        "in unknown queue")

                    else:
                        # abort it
                        await queue.abort_build(update.project_name, commit_hash)

                    # and store the new build id for that pull request
                    self._running_pull_builds[key] = (update.commit_hash, None)

            case QueueActions():
                # catch the queue of the build actions
                # only if we track that build, we store the queue

                # select the tracked build and store the learned queue
                for key, (commit_hash, _) in self._running_pull_builds.items():
                    if update.build_id == commit_hash:
                        self._running_pull_builds[key] = (commit_hash, update.queue)

            case BuildState():
                # build state to remove a running pull request
                if update.is_completed():
                    for key, (commit_hash, queue) in self._running_pull_builds.items():
                        if update.build_id == commit_hash:
                            # remove the build from the run list
                            del self._running_pull_builds[key]
                            return
