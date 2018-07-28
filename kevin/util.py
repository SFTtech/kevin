"""
Various utility functions.
"""

import logging
import os
import re
import tempfile

from pathlib import Path


# convenience infinity.
INF = float("inf")


def log_setup(setting, default=1):
    """
    Perform setup for the logger.
    Run before any logging.log thingy is called.

    if setting is 0: the default is used, which is WARNING.
    else: setting + default is used.
    """

    levels = (logging.ERROR, logging.WARNING, logging.INFO,
              logging.DEBUG, logging.NOTSET)

    factor = clamp(default + setting, 0, len(levels) - 1)
    level = levels[factor]

    logging.basicConfig(level=level, format="[%(asctime)s] %(message)s")
    logging.error("loglevel: %s", logging.getLevelName(level))
    logging.captureWarnings(True)


def clamp(number, smallest, largest):
    """ return number but limit it to the inclusive given value range """
    return max(smallest, min(number, largest))


# prefix to factor ** x map
SIZESUFFIX_POWER = {
    "": 0,
    "K": 1,
    "M": 2,
    "G": 3,
    "T": 4,
    "P": 5,
    "E": 6,
}


def parse_size(text):
    """
    parse a text like '10G' as 10 gigabytes = 10 * 1000**3 bytes
    returns size in bytes.
    """

    mat = re.match(r"(\d+)\s*([KMGTPE]?)(i?)B", text)
    if not mat:
        raise ValueError(
            "invalid size '%s', expected e.g. 10B, 42MiB or 1337KiB" % text)

    number = int(mat.group(1))
    suffix = mat.group(2)
    factor = 1024 if mat.group(3) else 1000

    power = SIZESUFFIX_POWER[suffix]

    return number * (factor ** power)


def parse_time(text, allow_inf=True):
    """
    parse a text like '10min' as 10 * 60 s
    returns time in seconds.
    """

    if allow_inf and text == "inf":
        return float("+inf")

    mat = re.match(r"(\d+)\s*(min|[hsm])", text)
    if not mat:
        raise ValueError(
            "invalid duration '%s', valid: 10min, 10m, 42h or 1337s" % text)

    number = int(mat.group(1))
    suffix = mat.group(2)

    factor = {"s": 1, "min": 60, "m": 60, "h": 3600}[suffix]
    return number * factor


class SSHKnownHostFile:
    """
    provide a temporary known hosts file for ssh
    """
    def __init__(self, host, port, key):
        self.host = host
        self.port = int(port)
        self.key = key
        self.tmpfile = None

    def create(self):
        """ Generate a temporary file with the key content """

        if self.key is not None:
            self.tmpfile = tempfile.NamedTemporaryFile(mode='w')

            # entry in the known hosts file
            key_data = "[%s]:%s %s\n" % (self.host, self.port, self.key)

            self.tmpfile.write(key_data)
            self.tmpfile.file.flush()

    def remove(self):
        """ Remove the generated file """
        if self.key is not None:
            self.tmpfile.close()

    def empty(self):
        """
        Return true if there is no key stored,
        i.e. no host key verification is performed because the key was None
        """
        return self.key is None

    def get_options(self):
        """ Return the ssh options to use this temporary known hosts file """

        if self.key is None:
            return [
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "StrictHostKeyChecking=no",
            ]

        if not self.tmpfile:
            raise Exception("SSHKnownHostFile::create() not called "
                            "or not SSHKnownHostFile not used 'with'")

        return [
            "-o", "UserKnownHostsFile=%s" % self.tmpfile.name,
            "-o", "StrictHostKeyChecking=yes",
        ]

    def __enter__(self):
        self.create()
        return self

    def __exit__(self, exc, value, traceback):
        self.remove()


def parse_connection_entry(name, entry, cfglocation=None, require_key=True,
                           protos=("unix", "ssh")):
    """
    parse a connection configuration entry.
    supported: unix and ssh.
    """

    if cfglocation is None:
        cfglocation = Path(".")

    def parse_ssh(match):
        """ parse the ssh connection entry """
        connection = "ssh"

        user = match.group(1)

        # (host, port)
        location = (match.group(2), int(match.group(3)))

        # ssh key entry or name of key file
        if match.group(4):
            key_entry = match.group(5).strip()

            if key_entry.startswith("ssh-"):
                key = key_entry
            else:
                # it's given as path to public key storage file
                path = Path(os.path.expanduser(key_entry))

                if not path.is_absolute():
                    path = cfglocation / path

                with open(str(path)) as keyfile:
                    key = keyfile.read().strip()
        else:
            if require_key:
                raise ValueError("For '%s=': please specify "
                                 "ssh key or keyfile with "
                                 "'... = $key or $filename'" % (name))

            # no key was given.
            key = None

        return user, connection, location, key

    def parse_unix(match):
        """ parse the unix connection entry """
        connection = "unix"

        if match.group(1):
            user = match.group(2)
        else:
            user = None

        location = match.group(3)

        return user, connection, location, None

    formats = {
        "ssh": (("ssh://user@host:port = ssh-rsa vmfingerprint "
                 "(or ~/.ssh/known_hosts)"),
                re.compile(r"ssh://(.+)@(.+):(\d+)\s*(=\s*(.*))?"),
                parse_ssh),

        "unix": ("unix://falkuser@/path/to/socket",
                 re.compile(r"unix://((.+)@)?(.+)"),
                 parse_unix),
    }

    for proto in protos:
        match = formats[proto][1].match(entry)

        if match:
            return formats[proto][2](match)

    raise ValueError("you wrote:\n'%s = %s'\n"
                     "-> you need to provide one of:\n    %s" % (
                         name, entry, "\n    ".join(
                             format[0] for format in formats.values()
                         )))


class AsyncWith:
    """
    Base class for objects that are usable with `async with` only.
    """

    def __enter__(self):
        raise Exception("use async with!")

    def __exit__(self, exc, value, traceback):
        raise Exception("use async with!")
