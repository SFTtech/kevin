"""
Project handling routines.
"""

from __future__ import annotations

import typing

from .action import Action
from .project_config import Config as ProjectConfig
from .trigger import Trigger

if typing.TYPE_CHECKING:
    from pathlib import Path
    from .config import Config as KevinConfig
    from .build import Build
    from .watcher import Watcher


class Project:
    """
    Represents a CI project, which has Builds that are started from Triggers,
    and to process the build, Actions are run.
    Watchers are subscribed to updates of this build.
    """

    def __init__(self, proj_cfg_path: Path, config: KevinConfig):

        self.cfg = ProjectConfig(proj_cfg_path)

        self.storage_path: Path = config.output_folder / self.cfg.project_name
        self.name = self.cfg.project_name

        # these will invoke a project build
        self.triggers: list[Trigger] = list()

        # these will receive or distribute build updates
        self.actions: list[Action] = list()

        # additional watchers to be attached
        self._watchers: list[Watcher] = list()

        # sort the services from the config in the appropriate list
        for service in self.cfg.get_services(self):
            match service:
                case Trigger():
                    self.triggers.append(service)
                case Action():
                    self.actions.append(service)
                case _:
                    raise Exception(f"configured project service is not a trigger or action: {service}")

    def add_watchers(self, watchers: list[Watcher]):
        """ Add actions manually as they may be created by e.g. triggers. """
        self._watchers.extend(watchers)

    async def attach_actions(self, build: Build, completed: bool):
        """
        Register all actions defined in this project
        so they receives updates from the build.
        The build may be complete already and some actions
        should not be attached then.
        """
        for action in self.actions:
            watcher = await action.get_watcher(build, completed)
            if watcher:
                await build.register_watcher(watcher)

        # attach additional watchers which were created by some trigger.
        for watcher in self._watchers:
            await build.register_watcher(watcher)

    def __str__(self):
        return f"<Project name={self.name}>"
