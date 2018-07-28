"""
Build action base class definition.
"""

from abc import abstractmethod

from .service_meta import Service


class Action(Service):
    """
    When a build produces updates, children of this class are used to perform
    some actions, e.g. sending mail, setting status, etc.
    """

    def __init__(self, cfg, project):
        super().__init__(cfg, project)

    @abstractmethod
    async def get_watcher(self, build, completed):
        """
        Return a watcher object which is then registered for build updates.
        """
        pass
