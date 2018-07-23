"""
VM management functionality
"""

from abc import ABCMeta, abstractmethod
import logging
import os
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

        config_keys = ("name", "ssh_user", "ssh_host", "ssh_port",
                       "ssh_known_host_key", "ssh_known_host_key_file")

        # set all config keys to None.
        for key in config_keys:
            setattr(self, key, None)

        # matches an /etc/ssh/ssh_host*_key.pub file
        # and ~/.ssh/known_hosts line
        # so that we can just extract the key.
        host_key_entry = (r"(?:.*)((?:ssh|ecdsa)-[^ ]+) "
                          r"([^\b@\. ]+) *(.*\b)?")

        host_key_pattern = re.compile(host_key_entry)


        # standard keys that exist for every machine.
        # more config options are specified in each container type,
        # e.g. Qemu, Xen, ...
        for key in config_keys:

            value = cfg.get(key)

            # legacy compatibility:
            # ssh_known_host_key was named ssh_key before.
            if key == "ssh_known_host_key" and not value:
                value = cfg.get("ssh_key")
                if value:
                    logging.warning("[vm] \x1b[33mwarning\x1b[m: "
                                    "'%s' uses deprecated option 'ssh_key', "
                                    "which is now called 'ssh_known_host_key'",
                                    self.machine_id)

            if not value:
                continue

            # ssh key loading:
            if key == "ssn_known_host_key":
                match = host_key_pattern.match(entry, value)
                if not match:
                    raise ValueError("malformed ssh_known_host_key entry: "
                                     "%s" % value)

                # use sanitized value
                value = " ".join(match.groups())

            if key == "ssh_known_host_key_file":
                if getattr(self, "ssh_known_host_key", None):
                    raise Exception("'ssh_known_host_key' already set, "
                                    "you can't set "
                                    "'ssh_known_host_key_file' then")

                # either the key is a file, or a line from the known hosts file.
                path = Path(os.path.expanduser(value))

                # determine location relative to the falk.conf
                if not path.is_absolute():
                    path = cfgpath / path

                if not path.is_file():
                    raise FileNotFoundError("known_host_key_file: %s" % path)

                with path.open() as keyfile:
                    known_hosts = keyfile.read().strip()

                    for line in known_hosts.split("\n"):
                        if not line.strip():
                            continue

                        match = host_key_pattern.match(entry, line)

                        if not match:
                            raise ValueError(
                                "wrong known_host_key_file format, "
                                "expected contents from a "
                                "/etc/ssh/ssh_host_*_key.pub file."
                            )

                        # craft entry as ssh-... KEYKEYKEY hostname
                        self.ssh_known_host_key = " ".join(match.groups())

            else:
                # simply copy the value from the config:
                setattr(self, key, value)

        # set default values for missing entries
        if not self.ssh_host:
            # if host is not specified, assume the falk localhost
            logging.warning("[vm] \x1b[33mwarning\x1b[m: "
                            "'%s' has no ssh_host specified, "
                            "assuming localhost",
                            self.machine_id)
            self.ssh_host = "localhost"


        if not self.ssh_known_host_key:
            logging.warning("[vm] \x1b[33mwarning\x1b[m: "
                            "'%s' doesn't have ssh-key configured, "
                            "thus I won't do a key verification!",
                            self.machine_id)
            self.ssh_known_host_key = None

        if not self.name:
            # if no name to "match" for is given, use the unique id.
            self.name = machine_id

        if not self.ssh_user:
            raise KeyError("[%s] config is missing 'ssh_user'" % (self.name))


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
        self.ssh_known_host_key = self.cfg.ssh_known_host_key
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
            "ssh_known_host_key": self.ssh_known_host_key,
        }

    @abstractmethod
    async def is_running(self):
        """
        Return if the container is still running.
        """
        pass

    @abstractmethod
    async def wait_for_shutdown(self, timeout=60):
        """
        Sleep for a maximum of `timeout` until the container terminates.
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
from . import qemu, xen, docker, lxc, clearlinux, nspawn, rkt, custom
