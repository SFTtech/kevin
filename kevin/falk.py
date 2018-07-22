"""
Code for interfacing with Falk instances to aquire VMs.
"""

from abc import ABC, abstractmethod
import asyncio
import logging

from falk.messages import (Message, ProtoType, Mode, Version, List,
                           Select, Status, OK, Login, Welcome, Error,
                           Exit)
from falk.protocol import FalkProto
from falk.vm import ContainerConfig

from .falkvm import FalkVM, FalkError
from .process import SSHProcess, ProcessError


class Falk(ABC):
    """
    VM provider instance.
    Provides a communication interface to request a VM.

    name: name of this Falk.
    """
    def __init__(self, name):
        self.name = name
        self.proto_mode = FalkProto.DEFAULT_MODE

        # list of callbacks that are invoked upon disconnect
        self.disconnect_callbacks = list()

        # set to false when this falk shall no longer be active
        # used for the reconnect mechanism in JobManager.
        self.active = True

        # there may only be one query to a falk at a time
        self.query_lock = asyncio.Lock()

    @abstractmethod
    async def create(self):
        """ create the falk connection """
        raise NotImplementedError()

    async def _init(self):
        """ Initialize the contacted falk and check if it is functional """

        welcomemsg = await self.query()
        if not isinstance(welcomemsg, Welcome):
            if welcomemsg is None:
                raise FalkError("no reply from falk!")
            else:
                raise FalkError("falk did not welcome us: %s" % welcomemsg)

        logging.info("[falk] '%s' says: %s",
                     welcomemsg.name,
                     welcomemsg.msg)

        # set to json mode.
        jsonset = await self.query(Mode("json"))
        if isinstance(jsonset, OK):
            self.proto_mode = ProtoType.json
        else:
            raise FalkError("failed setting json mode: %s" % jsonset)

        # do a version check
        vercheck = await self.query(Version(FalkProto.VERSION))
        if not isinstance(vercheck, OK):
            raise FalkError("incompatible falk contacted: %s" % vercheck)

    async def get_vms(self):
        """ return {name: type} """
        return (await self.query(List())).machines

    @abstractmethod
    def get_vm_host(self):
        """ Return the VM host by using the falk connection information """
        raise NotImplementedError()

    async def create_vm(self, machine_id):
        """
        Retrieve the machine list from falk and select one.

        falk: falk control connection
        machine_id: id of the machine to boot
        """

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
        async with self.query_lock:
            async for answer in self.send(msg, self.proto_mode):
                if isinstance(answer, Error):
                    raise FalkError("falk query failed: got %s" % answer)

                if not ret:
                    ret = answer
                    continue

                raise Exception("more than one answer message!")
        return ret

    @abstractmethod
    async def send(self, msg=None, mode=ProtoType.json):
        """
        Send a message to falk,
        This is an async iterator for the answer messages
        """
        raise NotImplementedError()

    @abstractmethod
    async def close(self):
        """
        Close the connection to falk.
        """
        raise NotImplementedError()

    def on_disconnect(self, callback):
        """
        When this falk disconnects, call the given callable.
        """

        if not callable(callback):
            raise ValueError(f"invalid callback: {callback}")

        self.disconnect_callbacks.append(callback)

    def connection_lost(self):
        """
        Falk was disconnected. Call all the callbacks.
        """

        for func in self.disconnect_callbacks:
            func(self)

        self.proto_mode = ProtoType.text

    async def __aenter__(self):
        await self.create()
        return self

    async def __aexit__(self, exc, value, traceback):
        # we ignore the query answer of exit
        await self.query(Exit())
        await self.close()


class FalkVirtual(Falk):
    """
    Dummy falk that is invoking the VM directly.
    That way, no separate falk process is needed, instead, the VM
    is launched by kevin (using falks code).

    TODO: implement :)
    """
    def __init__(self, name):
        super().__init__(name)

    async def create(self):
        raise NotImplementedError()

    async def send(self, msg=None, mode=ProtoType.json):
        raise NotImplementedError()

    def get_vm_host(self):
        return "localhost"

    def __str__(self):
        return f"<FalkVirtual {self.name} localhost>"


class FalkSSH(Falk):
    """
    Falk connection via ssh.
    we're then in a falk-shell to control the falk instance.
    """
    def __init__(self, name,
                 ssh_host, ssh_port, ssh_user, ssh_known_host_key,
                 loop=None):
        super().__init__(name)

        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.ssh_known_host_key = ssh_known_host_key

        self.ssh_process = None

        self.loop = loop or asyncio.get_event_loop()

    async def create(self):
        try:
            # connect to the falk host via ssh

            self.ssh_process = SSHProcess([], self.ssh_user, self.ssh_host,
                                          self.ssh_port, self.ssh_known_host_key,
                                          must_succeed=True,
                                          chop_lines=True,
                                          loop=self.loop)

            await self.ssh_process.create()
            # we don't need to send a login message as the falk.shell
            # already announced us.

            # perform falk setup
            await self._init()

            # when ssh exited, we might want to reconnect.
            self.ssh_process.on_exit(self.connection_lost)

        except ProcessError as exc:
            raise FalkError(f"Falk ssh process failed: {exc}") from exc

    async def send(self, msg=None, mode=ProtoType.json):
        try:
            if msg:
                msg = msg.pack(mode)

            # TODO: configurable timeout
            answers = self.ssh_process.communicate(
                data=msg,
                timeout=5,
                linecount=1,
            )

            async for stream, line in answers:
                if stream == 1 and line:
                    message = Message.construct(line, self.proto_mode)
                    yield message
                else:
                    logging.debug("\x1b[31mfalk ssh stderr\x1b[m: %s", line)
                    yield None

        except ProcessError as exc:
            raise FalkError(f"Failed to send request: {exc}") from exc

    def get_vm_host(self):
        return self.ssh_host

    async def close(self):
        try:
            if self.ssh_process:
                await self.ssh_process.pwn()
                self.ssh_process.cleanup()
            self.ssh_process = None

        except ProcessError as exc:
            raise FalkError(f"Failed to close connection: {exc}") from exc

    def __str__(self):
        return (f"<FalkSSH {self.name} "
                f"{self.ssh_user}@{self.ssh_host}:{self.ssh_port}>")


class FalkSocket(Falk):
    """
    Falk connection via unix socket.
    """
    def __init__(self, name, path, user, loop=None):
        super().__init__(name)

        self.path = path
        self.user = user

        self.transport = None
        self.protocol = None

        self.reader = None
        self.writer = None

        self.loop = loop or asyncio.get_event_loop()

    async def create(self):

        established = self.loop.create_future()

        def connection_made(reader, writer):
            """ called when the connection was made """
            self.reader = reader
            self.writer = writer
            established.set_result(True)

        try:
            (self.transport,
             self.protocol) = await self.loop.create_unix_connection(
                 lambda: FalkSocketStreamProtocol(
                     connection_made,
                     self.connection_lost
                 ), self.path)

        except FileNotFoundError:
            raise FileNotFoundError(
                "falk socket not found: "
                "'%s' missing" % self.path) from None
        except ConnectionRefusedError:
            raise FalkError("falk socket doesn't accept connections "
                            f"at '{self.path}'") from None

        await established

        # send login message, for FalkSSH it was not necessary
        # because the falk.shell automatically provides the login.
        msg = Login(self.user, self.path)
        self.writer.write(msg.pack(self.proto_mode))
        await self.writer.drain()

        # perform falk setup
        await self._init()

    async def send(self, msg=None, mode=ProtoType.json):
        if msg:
            self.writer.write(msg.pack(mode))
            await self.writer.drain()

        line = await self.reader.readline()
        message = Message.construct(line, self.proto_mode)
        yield message

    async def close(self):
        if self.transport and not self.transport.is_closing():
            self.transport.close()

    def get_vm_host(self):
        return "localhost"

    def __str__(self):
        return f"<FalkSocket {self.user}@{self.path}>"


class FalkSocketStreamProtocol(asyncio.StreamReaderProtocol):
    """
    Stream protocol used to control Falk over a stream.
    Used for connect and disconnect callbacks.
    """
    def __init__(self, connect_callback, disconnect_callback):
        super().__init__(asyncio.StreamReader(),
                         connect_callback)
        self.disconnect_callback = disconnect_callback

    def eof_received(self):
        # don't keep open
        return False

    def connection_lost(self, exc):
        super().connection_lost(exc)
        self.disconnect_callback()
