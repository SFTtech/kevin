"""
Code for interfacing with Falk instances to aquire VMs.
"""

import socket
from abc import ABC, abstractmethod

from .process import Process

from falk.control import VM, VMError
from falk.messages import (Message, ProtoType, Mode, Version, List,
                           Select, Status, OK, Login, Welcome, Error)
from falk.protocol import FalkProto, VERSION
from falk.vm import ContainerConfig


class FalkManager:
    """
    Keeps an overview about the reachable falks and their VMs.
    TODO: contact all known falks to fetch their provided machines
    TODO: cache the provided machines per falk.
    """

    def __init__(self):
        self.falks = set()

    def add_falk(self, falk):
        self.falks.add(falk)

    def remove_falk(self, falk):
        self.falks.remove(falk)


class Falk(ABC):
    """
    VM provider instance.
    Provides a communication interface to request a VM.
    """
    def __init__(self, job):
        # a job selected this falk server, now connect to it.

        self.job = job
        self.proto_mode = FalkProto.DEFAULT_MODE

    def init(self):
        """ Initialize the contacted falk and check if it is functional """

        welcomemsg = self.query()
        if not isinstance(welcomemsg, Welcome):
            raise Exception("falk did not welcome us: %s" % welcomemsg)

        print("[falk] provider '%s' says: %s" % (welcomemsg.name, welcomemsg.msg))

        # set to json mode.
        jsonset = self.query(Mode("json"))
        if isinstance(jsonset, OK):
            self.proto_mode = ProtoType.json
        else:
            raise Exception("failed setting json mode: %s" % jsonset)

        # do a version check
        vercheck = self.query(Version(VERSION))
        if not isinstance(vercheck, OK):
            raise Exception("incompatible falk contacted: %s" % vercheck)

    def get_vms(self):
        """ return {name: type} """
        # TODO: use/update cache
        return self.query(List()).machines

    @abstractmethod
    def get_vm_host(self):
        """ Return the VM host by using the falk connection information """
        raise NotImplementedError()

    def create_vm(self, machine_name, explicit=False):
        """
        Retrieve the machine list from falk and select one.

        If explicit is True, machine_name will be used as machine_id,
        which is the unique VM identifier for this falk.
        """

        falk_vms = self.get_vms()

        machine_id = None

        # match by name, not by id:
        if not explicit:
            # try to find the machine in the list:
            for vm_id, (_, name) in falk_vms.items():

                # here, do the name match:
                if machine_name == name:
                    machine_id = vm_id

            if machine_id is None:
                return None
        else:
            machine_id = machine_name

        if machine_id not in falk_vms.keys():
            raise Exception("requested VM not found.")

        # create vm handle in the remote falk.
        run_id = self.query(Select(machine_id)).run_id

        # fetch status and ssh config for created vm handle.
        cfg = self.query(Status(run_id)).dump()

        # falk says VM is running on his "localhost",
        # so we replace that with falks address.
        if cfg["ssh_host"] == "localhost":
            cfg["ssh_host"] = self.get_vm_host()

        # create the machine config
        config = ContainerConfig(machine_id, cfg, None)

        # create machine from the config
        return VM(config, run_id, self)

    def query(self, msg=None):
        """ Send some message to falk and retrieve the answer message."""
        answers = self.send(msg, self.proto_mode)
        ret = None
        for answer in answers:
            if isinstance(answer, Error):
                raise RuntimeError("error: %s" % answer)
            if not ret:
                ret = answer
                continue
            print("\x1b[31mignored leftover answer: %s\x1b[m" % (answer))
        return ret

    @abstractmethod
    def send(self, msg=None, mode=ProtoType.json):
        """ Send a message to falk, yield the answer messages """
        raise NotImplementedError()


class FalkVirtual(Falk):
    """
    Dummy falk that is invoking the VM directly.
    That way, no separate falk process is needed, instead, the VM
    is launched by kevin (using falks code).

    TODO: implement :)
    """
    def __init__(self, job):
        super().__init__(job)

    def send(self, msg=None, mode=ProtoType.json):
        raise NotImplementedError()

    def get_vm_host(self):
        return "localhost"


class FalkSSH(Falk):
    """
    Falk connection via ssh.
    we're then in a falk-shell to control the falk instance.
    """
    def __init__(self, job, ssh_host, ssh_port, ssh_user):
        super().__init__(job)

        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user

        # connect to the actual falk host
        # TODO: make use of the ssh knownhost generator?
        self.connection = Process([
            "ssh",
            "-p", str(self.ssh_port),
            self.ssh_user + "@" + self.ssh_host,
        ])

        self.init()

    def send(self, msg=None, mode=ProtoType.json):
        if msg:
            msg = msg.pack(mode)

        # TODO: configurable timeout
        answers = self.connection.communicate(
            data=msg,
            timeout=10,
            linecount=1,
        )

        for stream, line in answers:
            if stream == 1 and line:
                message = Message.construct(line, self.proto_mode)
                print("falk: %s" % message)
                yield message
            else:
                print("falk error: %s" % line)

    def get_vm_host(self):
        return self.ssh_host


class FalkSocket(Falk):
    """
    Falk connection via unix socket.
    """
    def __init__(self, job, path, user):
        super().__init__(job)

        self.path = path
        self.user = user
        self.linebuf = bytearray()

        # connect to falk unix socket.
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        try:
            self.sock.connect(self.path)
        except FileNotFoundError:
            raise FileNotFoundError(
                "falk socket not found: "
                "'%s' missing" % self.path) from None
        except ConnectionRefusedError:
            raise VMError("falk socket doesn't accept connections "
                          "at '%s'" % (self.path)) from None

        # send login message
        msg = Login(self.user, self.path)
        self.sock.sendall(msg.pack(self.proto_mode))

        self.init()

    def send(self, msg=None, mode=ProtoType.json):
        if msg:
            self.sock.sendall(msg.pack(mode))

        while True:
            npos = self.linebuf.rfind(b"\n")

            if npos < 0:
                data = self.sock.recv(8196)
                if not data:
                    raise Exception("connection closed!")
                self.linebuf.extend(data)

            else:
                for line in self.linebuf[:npos].split(b"\n"):
                    if line:
                        recv = Message.construct(line, self.proto_mode)
                        yield recv

                del self.linebuf[:(npos+1)]
                break

    def get_vm_host(self):
        return "localhost"
