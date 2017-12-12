"""
VM management functionality
"""

from abc import ABCMeta, abstractmethod
import logging
import re
from pathlib import Path


# container class name -> class mapping
# it's populated by the metaclass below,
# which is triggered by the imports at the end of this file.
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
    Guarantees the existence of:
     * VM access data (SSH)
     * Machine ID     (id unique in this falk instance)
     * Machine Name   (to match for)

    Created from a config dict that contains key-value pairs.
    """
    def __init__(self, machine_id, cfg, cfgpath):

        # store the machine id
        self.machine_id = machine_id
        self.cfgpath = cfgpath

        # standard keys that exist for every machine.
        # more config options are specified in each container type,
        # e.g. Qemu, Xen, ...
        for key in ("name", "ssh_user", "ssh_host", "ssh_port", "ssh_key"):

            # test if the key is in the file
            if key in cfg:

                # ssh key loading:
                if key == "ssh_key":
                    # it's either the key directly or a file
                    sshkey_entry = cfg[key]
                    if sshkey_entry.startswith("ssh-"):
                        # it's directly stored
                        self.ssh_key = sshkey_entry
                    else:
                        # it's given as path to public key storage file
                        path = Path(sshkey_entry)

                        # determine location relative to the falk.conf
                        if not path.is_absolute():
                            path = cfgpath / path

                        with open(str(path)) as keyfile:
                            self.ssh_key = keyfile.read().strip()

                # simply copy the value from the config:
                else:
                    setattr(self, key, cfg[key])

                continue

            elif key == "ssh_host":
                # if host is not specified, assume the falk localhost
                self.ssh_host = "localhost"

            elif key == "ssh_port":
                self.ssh_port = None

            elif key == "ssh_key":
                logging.warning("[vm] \x1b[33mwarning\x1b[m: "
                                "'%s' doesn't have ssh-key configured, "
                                "making key check impossible!",
                                self.machine_id)
                self.ssh_key = None

            elif key == "name":
                # if no name to "match" for is given, use the unique id.
                self.name = machine_id

            else:
                raise KeyError("%s config is missing %s=" % (
                    self.name, key))


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
        self.ssh_key = self.cfg.ssh_key
        self.ssh_port = self.cfg.ssh_port

        if self.ssh_port is None:
            raise ValueError("ssh port not yet set!")
        if self.ssh_user is None:
            raise ValueError("ssh user not set!")

    @classmethod
    def containertype(cls):
        """ Generates the common type name of this container """
        return cls.__name__.lower()

    @classmethod
    @abstractmethod
    def config(cls, machine_id, cfgdata, cfgpath):
        """
        Create configuration dict for this container type.
        This method allows container-type specific config options.

        machine_id: the unique id of the machine in the cfgfile
        cfgdata: key-value pairs from configuration file
        cfgpath: folder where the config files was in
        returns: ContainerConfig object
        """
        raise NotImplementedError()

    @abstractmethod
    async def prepare(self, manage=False):
        """
        Prepares the launch of the container,
        e.g. by creating a temporary runimage.
        """
        pass

    @abstractmethod
    async def launch(self):
        """ Launch the virtual machine container """
        pass

    async def status(self):
        """ Return runtime information for the container """

        return {
            "running": await self.is_running(),
            "ssh_user": self.ssh_user,
            "ssh_host": self.ssh_host,
            "ssh_port": self.ssh_port,
            "ssh_key": self.ssh_key,
        }

    @abstractmethod
    async def is_running(self):
        """
        Return if the container is still running.
        """
        pass

    @abstractmethod
    async def terminate(self):
        """ Terminate the container if it doesn't shutdown on its own """
        pass

    @abstractmethod
    async def cleanup(self):
        """ Cleanup the container, e.g. remove tmpfiles. """
        pass


# force class definitions too fill CONTAINERS dict
from . import qemu, xen, docker, lxc, clearlinux, nspawn, rkt
