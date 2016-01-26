"""
Project handling routines.
"""

from configparser import ConfigParser

from .config import Config
from ..service import (Trigger, Action)


class Project:
    """
    Represents a coding project.
    """

    def __init__(self, configpath):
        self.cfg = Config(configpath, self)

        # these will invoke a project build
        self.triggers = list()

        # these will receive or distribute build updates
        self.actions = list()

        for service in self.cfg.services:
            if isinstance(service, Trigger):
                self.triggers.append(service)
            elif isinstance(service, Action):
                self.actions.append(service)
            else:
                raise Exception("service is not a trigger or action")

    @property
    def name(self):
        """ return the unique project name """
        return self.cfg.project_name

    def attach_actions(self, watchable):
        """
        Register all requested actions to the update provider to receive
        progress updates.
        """
        for action in self.actions:
            watcher = action.get_watcher(watchable)
            watchable.watch(watcher)
