"""
Supported service base definitions.
"""


from abc import ABCMeta, abstractmethod

# service name => service class mapping
# the name equals the key in your [triggers] and [actions]
# config sections.
# the name is fetched with the name() method of a service.
SERVICES = dict()


class ServiceMeta(ABCMeta):
    """
    Service metaclass.
    Adds the service message types to the lookup dict.

    It creates entries in the SERVICES dict to allow easy fetching by
    name.
    """
    def __init__(cls, name, bases, classdict):
        super().__init__(name, bases, classdict)
        # ignore the abstract classes
        if name not in {"Service", "Trigger", "HookTrigger", "Action"}:
            entry = cls.name()
            if entry in SERVICES:
                raise Exception("redefinition of service '%s'" % entry)
            SERVICES[entry] = cls


class Service(metaclass=ServiceMeta):
    """
    Base class for all services for a project.
    A service is e.g. a IRC notification,
    a build trigger via some webhook, etc.
    """

    @classmethod
    @abstractmethod
    def name(cls):
        """
        Return the service name.
        This is the key that has to be placed in the project config.
        the value of that key is the config filename containing
        stuff about this service.
        """
        pass

    def __init__(self, cfg, project):
        from ..project import Project
        if type(project) != Project:
            raise TypeError("project has invalid type '%s'" % (type(project)))
        self.project = project

    def get_project(self):
        """ Return the associated project """
        return self.project


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

    def merge_cfg(self):
        """
        Perform merge operations so that this trigger only functions as
        a config for another class that is instanciated later.
        E.g. the config for all the webhooks is defined multiple times
        as a trigger, but the server waiting for it is only created once.
        This function prepares this merging.
        """
        pass


class Action(Service):
    """
    When a build produces updates, children of this class are used to perform
    some actions, e.g. sending mail, setting status, etc.
    """

    def __init__(self, cfg, project):
        super().__init__(cfg, project)

    @abstractmethod
    def get_watcher(self, build):
        """
        Return a watcher object which is then registered for build updates.
        """
        pass
