"""
Build trigger base class definition.
"""

from abc import abstractmethod

from .service_meta import Service


class Trigger(Service):
    """
    Base class for all project build triggers.
    These can start a build by some means, either by external notification,
    or by active polling.
    """

    @classmethod
    @abstractmethod
    def name(cls):
        pass

    def __init__(self, cfg, project):
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
