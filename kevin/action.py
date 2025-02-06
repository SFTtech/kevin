"""
Build action base class definition.
"""

from __future__ import annotations

import typing
from abc import abstractmethod

from .service import Service

if typing.TYPE_CHECKING:
    from .watcher import Watcher
    from .project import Project
    from .build import Build


class Action(Service):
    """
    When a build produces updates, children of this class are used to perform
    some actions, e.g. sending mail, setting status, etc.
    """

    def __init__(self, cfg: dict[str, str], project: Project):
        super().__init__(cfg, project)

    @abstractmethod
    async def get_watcher(self, build: Build, completed: bool) -> Watcher | None:
        """
        Return a watcher object which is then registered for build updates.
        """
        pass
