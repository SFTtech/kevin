"""
GitHub interaction entry points for Kevin.
"""

from __future__ import annotations

import typing

from .pull_manager import GitHubPullManager
from .webhook import GitHubHookHandler
from .status import GitHubBuildStatusUpdater
from ...action import Action
from ...httpd import HookTrigger
from ...watcher import Watcher

if typing.TYPE_CHECKING:
    from ...build import Build
    from ...project import Project


class GitHubHook(HookTrigger):
    """
    A trigger from a GitHub webhook.
    This class is instanced multiple times, maybe even more
    for one project.

    Having one of those for each project is the normal case.
    """

    def __init__(self, cfg, project) -> None:
        super().__init__(cfg, project)

        # shared secret
        self.hooksecret: bytes = cfg["hooksecret"].encode()

        # allowed github repos
        self.repos: set[str] = set()
        for repo in cfg["repos"].split(","):
            repo = repo.strip()
            if repo:
                self.repos.add(repo)

        # assign labelname => action for custom control labels
        self.ctrl_labels = dict()
        for label in cfg.get("ctrl_labels_rebuild", "").split(','):
            label = label.strip()
            if label:
                self.ctrl_labels[label] = "rebuild"

        # pull request manager to detect build aborts
        self.pull_manager = GitHubPullManager(self.repos)

    def get_watchers(self):
        return [self.pull_manager]

    def get_handler(self):
        return ("/hooks/github", GitHubHookHandler)


class GitHubStatus(Action):
    """
    GitHub status updater action, enable in a project to
    allow real-time build updates via the github api.
    """
    def __init__(self, cfg, project: Project) -> None:
        super().__init__(cfg, project)
        self.auth_user = cfg["user"]
        self.auth_pass = cfg["token"]
        self.repos: set[str] = set()
        for repo in cfg.get("repos", "any").split(","):
            repo = repo.strip()
            if repo:
                self.repos.add(repo)

    async def get_watcher(self, build: Build, completed: bool) -> Watcher | None:
        # we return a watcher even if the build is completed already
        # -> a second pull request for the same build is reported as completed without a rebuild..
        return GitHubBuildStatusUpdater(build, self)
