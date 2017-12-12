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
        """
        project must be a project.Project.
        """
        del cfg  # currently unused. subclasses use it, though.
        self.project = project

    def get_project(self):
        """ Return the associated project """
        return self.project
