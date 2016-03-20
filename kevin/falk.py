"""
Code for interfacing with Falk instances to aquire VMs.
"""

import asyncio
import logging
from abc import ABC, abstractmethod

from .falkvm import FalkVM, VMError
from .process import SSHProcess
from .util import asynciter, AsyncChain

from falk.messages import (Message, ProtoType, Mode, Version, List,
                           Select, Status, OK, Login, Welcome, Error,
                           Exit)
from falk.protocol import FalkProto
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
    def __init__(self):
        self.proto_mode = FalkProto.DEFAULT_MODE

    @abstractmethod
    async def create(self):
        """ create the falk connection """
        raise NotImplementedError()

    async def init(self):
        """ Initialize the contacted falk and check if it is functional """

        welcomemsg = await self.query()
        if not isinstance(welcomemsg, Welcome):
            if welcomemsg is None:
                raise VMError("no reply from falk!")
            else:
                raise VMError("falk did not welcome us: %s" % welcomemsg)

        logging.info("[falk] '%s' says: %s" % (welcomemsg.name,
                                               welcomemsg.msg))

        # set to json mode.
        jsonset = await self.query(Mode("json"))
        if isinstance(jsonset, OK):
            self.proto_mode = ProtoType.json
        else:
            raise VMError("failed setting json mode: %s" % jsonset)

        # do a version check
        vercheck = await self.query(Version(FalkProto.VERSION))
        if not isinstance(vercheck, OK):
            raise VMError("incompatible falk contacted: %s" % vercheck)

    async def get_vms(self):
        """ return {name: type} """
        # TODO: use/update cache
        return (await self.query(List())).machines

    @abstractmethod
    def get_vm_host(self):
        """ Return the VM host by using the falk connection information """
        raise NotImplementedError()

    async def create_vm(self, machine_name, explicit=False):
        """
        Retrieve the machine list from falk and select one.

        explicit: True -> machine_name will be used as machine_id,
                          which is the unique VM identifier for this falk.
                  False -> machine_name is tried to be matched against
                           all available machines.
        """

        falk_vms = await self.get_vms()

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
        run_id = (await self.query(Select(machine_id))).run_id

        # fetch status and ssh config for created vm handle.
        cfg = (await self.query(Status(run_id))).dump()

        # falk says VM is running on his "localhost",
        # so we replace that with falks address.
        if cfg["ssh_host"] == "localhost":
            cfg["ssh_host"] = self.get_vm_host()

        # create the machine config
        config = ContainerConfig(machine_id, cfg, None)

        # create machine from the config
        return FalkVM(config, run_id, self)

    async def query(self, msg=None):
        """ Send some message to falk and retrieve the answer message."""
        ret = None
        async for answer in self.send(msg, self.proto_mode):
            if isinstance(answer, Error):
                raise VMError("falk query failed: got %s" % answer)

            if not ret:
                ret = answer
                continue

            raise Exception("more than one answer message!")
        return ret

    @abstractmethod
    def send(self, msg=None, mode=ProtoType.json):
        """
        Send a message to falk,
        return an async iterator for the answer messages
        """
        raise NotImplementedError()

    async def __aenter__(self):
        await self.create()
        return self

    async def __aexit__(self, exc, value, tb):
        answer = await self.query(Exit())
        pass


class FalkVirtual(Falk):
    """
    Dummy falk that is invoking the VM directly.
    That way, no separate falk process is needed, instead, the VM
    is launched by kevin (using falks code).

    TODO: implement :)
    """
    def __init__(self):
        super().__init__()

    async def create(self):
        raise NotImplementedError()

    def send(self, msg=None, mode=ProtoType.json):
        raise NotImplementedError()

    def get_vm_host(self):
        return "localhost"


class FalkSSH(Falk):
    """
    Falk connection via ssh.
    we're then in a falk-shell to control the falk instance.
    """
    def __init__(self, ssh_host, ssh_port, ssh_user, ssh_key):
        super().__init__()

        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.ssh_key = ssh_key

        # connect to the falk host via ssh
        self.connection = SSHProcess([], self.ssh_user, self.ssh_host,
                                     self.ssh_port, self.ssh_key,
                                     chop_lines=True)

    async def create(self):
        await self.connection.create()
        # we don't need to send a login message as the falk.shell
        # already announced us.

        # perform falk setup
        await self.init()

    def send(self, msg=None, mode=ProtoType.json):
        if msg:
            msg = msg.pack(mode)

        # TODO: configurable timeout
        answers = self.connection.communicate(
            data=msg,
            timeout=10,
            linecount=1,
        )

        # hack to work around the impossibility to
        # loop through `async for` and then yield values
        def construct(inp):
            stream, line = inp
            if stream == 1 and line:
                message = Message.construct(line, self.proto_mode)
                return message
            else:
                logging.debug("\x1b[31mfalk ssh stderr\x1b[m: %s" % line)
                return None

        return AsyncChain(answers, construct)

    def get_vm_host(self):
        return self.ssh_host

    def __str__(self):
        return "Falk ssh <%s@%s:%s>" % (
            self.ssh_user,
            self.ssh_host,
            self.ssh_port,
        )


class FalkSocket(Falk):
    """
    Falk connection via unix socket.
    """
    def __init__(self, path, user):
        super().__init__()

        self.path = path
        self.user = user

        self.transport = None
        self.protocol = None

        self.reader = None
        self.writer = None

    async def create(self):

        established = asyncio.Future()

        def connection_made(reader, writer):
            """ called when the connection was made """
            self.reader = reader
            self.writer = writer
            established.set_result(True)

        loop = asyncio.get_event_loop()

        try:
            self.transport, self.protocol = await loop.create_unix_connection(
                lambda: asyncio.StreamReaderProtocol(
                    asyncio.StreamReader(),
                    connection_made
                ), self.path)

        except FileNotFoundError:
            raise FileNotFoundError(
                "falk socket not found: "
                "'%s' missing" % self.path) from None
        except ConnectionRefusedError:
            raise VMError("falk socket doesn't accept connections "
                          "at '%s'" % (self.path)) from None

        await established

        # send login message, for FalkSSH it was not necessary
        # because the falk.shell automatically provides the login.
        msg = Login(self.user, self.path)
        self.writer.write(msg.pack(self.proto_mode))
        await self.writer.drain()

        # perform falk setup
        await self.init()

    @asynciter
    def send(self, msg=None, mode=ProtoType.json):
        if msg:
            self.writer.write(msg.pack(mode))
            yield from self.writer.drain()

        line = yield from self.reader.readline()
        message = Message.construct(line, self.proto_mode)
        yield message

    def get_vm_host(self):
        return "localhost"

    def __str__(self):
        return "Falk socket <%s@%s>" % (self.user, self.path)
