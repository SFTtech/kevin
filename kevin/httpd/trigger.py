"""
Base web HookTrigger definition
"""

from abc import abstractmethod
from tornado import web

from ..service import Trigger


class HookTrigger(Trigger):
    """
    Base class for a webhook trigger (e.g. the github thingy).
    """

    def __init__(self, cfg, project):
        super().__init__(cfg, project)

    @abstractmethod
    def get_handler(self):
        """
        Return the (url, HookHandler class) to register at tornado for webhooks
        """
        pass

    def add_args(self, kwargdict):
        """
        Let the hook trigger add itself to the existing kwarg dict
        which will be passed to the class returned in `get_handler()`
        when instanciated.
        """
        return kwargdict

    def merge_cfg(self, urlhandlers):
        # when e.g. GitHubHookHandler is instanciated,
        # the list of all Triggers that use it
        # will be passed as configuration.

        # create an entry in the defaultdict for this
        # hook handler class, e.g. GitHubHookHandler.
        handlerkwargs = urlhandlers[self.get_handler()]

        # and add the config which requested it to the list
        # this step creates the mandatory "triggers" constructor
        # argument for all HookHandlers.
        handlerkwargs["triggers"].append(self)

        # additional custom keyword arguments for this
        urlhandlers[self.get_handler()] = self.add_args(handlerkwargs)


class HookHandler(web.RequestHandler):
    """
    Base class for web hook handlers.
    A web hook is a http request made by e.g. github, gitlab, ...
    and notify kevin that there's a job to do.
    """

    def initialize(self, triggers):
        """
        triggers: a list of HookTriggers which requested to instanciate
                  this HookHandler
        """
        raise NotImplementedError()

    def get(self):
        raise NotImplementedError()

    def post(self):
        raise NotImplementedError()
