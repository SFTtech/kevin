"""
In this module are implementations of supported services.
Those can be triggers, actions or both.
"""

from __future__ import annotations

import typing
from abc import ABC

if typing.TYPE_CHECKING:
    from ..project import Project


class Service(ABC):
    """
    Base class for all services for a project.
    A service is e.g. a IRC notification,
    a build trigger via some webhook, etc.
    """

    def __init__(self, cfg: dict[str, str], project: Project):
        del cfg  # unused here. subclasses use it, though.
        self.project = project

    def get_project(self) -> Project:
        """ Return the associated project """
        return self.project
