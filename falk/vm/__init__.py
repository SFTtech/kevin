"""
VM management functionality
"""

from abc import ABCMeta, abstractmethod
import re


# container class name -> class mapping
CONTAINERS = dict()


class ContainerMeta(ABCMeta):
    """
    Metaclass for available container backends.
    """
    def __init__(cls, name, bases, classdict):
        super().__init__(name, bases, classdict)
        CONTAINERS[cls.containertype()] = cls


class ContainerConfig:
    """
    Configuration for a container.
    Guarantees the existence of access data.

    Created from a config dict that contains key-value pairs.
    """
    def __init__(self, name, cfg, keep_port=False):
        self.name = name
        for key in ("ssh_user", "ssh_host", "ssh_port"):
            if key in cfg:
                setattr(self, key, cfg[key])

            elif key == "ssh_host":
                # implicit default:
                self.ssh_host = "localhost"

            else:
                raise KeyError("%s config is missing %s=" % (
                    self.name, key))

        # ssh ports may be a range or a single port
        mat = re.match(r"\[(\d+),(\d+)\]", str(self.ssh_port))
        if mat:
            # port range
            lower, upper = int(mat.group(1)), int(mat.group(2))
            if not lower < upper:
                raise ValueError("invalid port range (>): [%d,%d]" % (lower,
                                                                      upper))
            self.ssh_port_range = lower, upper
        else:
            # single port
            port = int(self.ssh_port)
            self.ssh_port_range = port, port

        if not keep_port:
            # must be set before the actual run, so it's unset again.
            self.ssh_port = None

    def set_port(self, port):
        """ Set the actual ssh port to be used. """
        self.ssh_port = port


class Container(metaclass=ContainerMeta):
    """
    Base class for build worker machines.

    Guarantees the same management functionality for all
    used implementations, e.g. qemu, docker, xen, ...

    Objects are instanced for each running machine.
    The classmethods below are for configuration creation
    for all machines of the same type.
    """

    def __init__(self, cfg):
        if not isinstance(cfg, ContainerConfig):
            raise ValueError("not a container config: %s" % cfg)
        self.cfg = cfg
        self.ssh_user = self.cfg.ssh_user
        self.ssh_host = self.cfg.ssh_host

        if self.cfg.ssh_port is None:
            raise ValueError("ssh port not yet set!")
        else:
            self.ssh_port = self.cfg.ssh_port

    @classmethod
    def containertype(cls):
        """ Generates the common type name of this container """
        return cls.__name__.lower()

    @classmethod
    def config(cls, cfgdata):
        """
        Create configuration dict for this container type.
        This method allows container-type specific config options.

        cfgdata: key-value pairs from configuration file
        returns: ContainerConfig object.
        """
        raise NotImplementedError()

    @abstractmethod
    def prepare(self, manage=False):
        """
        Prepares the launch of the container,
        e.g. by creating a temporary runimage.
        """
        pass

    @abstractmethod
    def launch(self):
        """ Launch the virtual machine container """
        pass

    @abstractmethod
    def status(self):
        """ Return information about the running container """
        pass

    @abstractmethod
    def terminate(self):
        """ Terminate the container if it doesn't shutdown on its own """
        pass

    @abstractmethod
    def cleanup(self):
        """ Cleanup the container, e.g. remove tmpfiles. """
        pass


# force class definitions too fill CONTAINERS dict
from . import qemu, xen, docker, lxc, clearlinux, nspawn, rkt
