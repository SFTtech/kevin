"""
Build trigger base class definition.
"""

from __future__ import annotations

import typing

from .service import Service

if typing.TYPE_CHECKING:
    from .project import Project


class Trigger(Service):
    """
    Base class for all project build triggers.
    These can start a build by some means, either by external notification,
    or by active polling.
    """

    def __init__(self, cfg: dict[str, str], project: Project):
        super().__init__(cfg, project)

    def get_watchers(self):
        """
        Return a list of watcher objects. Those will be attached additionally
        to the other watchers returned by some action.  That way, e.g. a
        github trigger can attach a pull request watcher.
        """
        return []

    def merge_cfg(self, urlhandlers):
        """
        Perform merge operations so that this trigger only functions as
        a config for another class that is instanciated later.
        E.g. the config for all the webhooks is defined multiple times
        as a trigger, but the server waiting for it is only created once.
        This function prepares this merging.
        """
        pass
