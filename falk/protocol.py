"""
Falk communication protocol.
"""

import asyncio
import copy
import logging
import traceback

from . import messages
from .config import CFG
from .messages import ProtoType


class FalkProto(asyncio.Protocol):
    """
    Communication protocol for falks control socket.
    This is created for each connection on the socket.

    If the connection dies, all associated containers will be killed.
    """

    # default: user-friendly text mode.
    DEFAULT_MODE = ProtoType.text

    # message protocol version
    VERSION = 0

    def __init__(self, falk, loop=None):
        self.falk = falk
        self.loop = loop or asyncio.get_event_loop()

        # line buffer
        self.buf = bytearray()
        self.maxbuf = (8 * 1024 * 1024)  # 8 MiB max buffer

        # falk user identification
        self.user = None
        self.ip = None
        self.source = None

        # containers running in this session
        # contains {handle_id: vmname, ...}
        self.running = dict()

        # set by the Select message
        self.current_machine = None

        # current transmission protocol mode
        self.mode = self.DEFAULT_MODE

        # internal connection handle id
        self.conn_id = None

        # command queue. gets filled from the control socket.
        self.queue = asyncio.Queue()

        # is the connection still alive?
        self.disconnected = self.loop.create_future()

        # transport and connection tracking
        self.connected = False
        self.transport = None

    def connection_made(self, transport):
        self.connected = True
        self.conn_id = self.falk.get_connection_id()

        self.log("new client connected")

        self.transport = transport

    def data_received(self, data):
        if not data.strip():
            return

        if len(self.buf) + len(data) > self.maxbuf:
            self.send(messages.Error("too much data."))
            self.close()
            return

        self.buf.extend(data)

        newline = self.buf.rfind(b"\n")
        if not newline >= 0:
            return

        # pick data till last newline and keep the rest.
        msgs = bytes(self.buf[:newline])
        self.buf = self.buf[newline + 1:]

        for line in msgs.split(b"\n"):
            # each line is a command
            try:
                if line.strip():
                    msg = messages.Message.construct(line, self.mode)
                    self.queue.put_nowait(msg)

            except Exception as exc:
                self.log("\x1b[31;1mfailed constructing message:\x1b[m",
                         logging.ERROR)
                traceback.print_exc()
                self.send(messages.Error(repr(exc)))

    def connection_lost(self, exc):
        self.log("lost connection")
        if exc:
            self.log("reason: %s" % repr(exc))
            self.disconnected.set_exception(exc)

        else:
            self.disconnected.set_result(StopIteration)

    def send(self, msg, force_mode=None):
        """
        Sends a protocol message to the falk client.
        """

        data = msg.pack(force_mode or self.mode)
        self.transport.write(data)

    def close(self):
        """
        Terminates the connection
        """

        self.log("terminating connection..")
        self.transport.close()

    def log(self, msg, level=logging.INFO):
        """
        logs something for this connection
        """

        logging.log(level, "[\x1b[1m%3d\x1b[m] %s", self.conn_id, msg)

    def check_version(self, msg):
        """
        Checks if the given version is compatible with our one.
        """

        if not msg.version == self.VERSION:
            return "unmatching versions: you=%d != us=%d" % (msg.version,
                                                             self.VERSION)

        return None

    def set_mode(self, msg):
        """
        sets the protocol transmission mode.
        """

        if msg.mode != self.mode:
            if msg.mode == "json":
                self.mode = ProtoType.json
            elif msg.mode == "text":
                self.mode = ProtoType.text
            else:
                return "unknown mode"
        return None

    def get_machine_id(self, run_id=None):
        """
        Stupid helper function:
        Selects the running machine id either by using the
        current machine or the given one.
        """
        if run_id is None:
            if self.current_machine is not None:
                return self.current_machine
            else:
                raise IndexError("No machine is currently selected.")
        else:
            return run_id

    def get_machine(self, run_id):
        """
        Fetch the running machine instance with given id.
        """
        return self.falk.get_machine(self.get_machine_id(run_id))

    async def kill_containers(self):
        """
        Make sure all containers spawned by this control connection are dead.
        """

        for handle_id, name in self.running.items():
            container = self.get_machine(handle_id)

            self.log("killing container %s..." % name)
            await container.terminate()
            await container.cleanup()

            # remove the handle
            self.falk.delete_handle(handle_id)

    async def process_messages(self):
        """
        Fetch messages from the queue and react to their request.

        We can't simply create a new task for every message in the
        data_received function, because we always need to cancel
        a running task.
        """

        while not self.disconnected.done():

            queue_task = asyncio.get_event_loop().create_task(
                self.queue.get())

            try:
                # either the connection was lost or
                # we got a command in the queue
                done, pending = await asyncio.wait(
                    [queue_task, self.disconnected],
                    return_when=asyncio.FIRST_COMPLETED)

            except asyncio.CancelledError:
                # ensure cancellation... wtf whyyy
                # asyncio pls...
                #
                # if the process_messages task is cancelled,
                # the self.queue.get() might be pending and gets destroyed
                # which results in an error.
                # if you are an asyncio expert, please fix it if you can.

                if not queue_task.done():
                    queue_task.cancel()

                raise

            for future in done:
                exc = future.exception()
                if exc:
                    for future_pending in pending:
                        future_pending.cancel()

                    await self.kill_containers()

                    raise exc

                message = future.result()
                if message == StopIteration:
                    if not self.disconnected.done():
                        self.disconnected.set_result(message)

                    for future_pending in pending:
                        future_pending.cancel()

                    await self.kill_containers()

                    break

                # regular message
                try:
                    answer, force_mode = await self.control_message(message)
                    self.send(answer, force_mode)

                except Exception as exc:
                    self.log("\x1b[31;1merror processing request:\x1b[m",
                             logging.ERROR)
                    traceback.print_exc()
                    self.send(messages.Error(repr(exc)))

    async def control_message(self, msg):
        """
        Parse and handle falk control messages.
        This defines falks behavior!

        """

        self.log("processing message: %s" % msg, level=logging.DEBUG)

        force_mode = None
        answer = messages.OK()

        if isinstance(msg, messages.Error):
            self.log("peer failed: %s" % msg)
            self.close()

        elif isinstance(msg, messages.Help):
            answer = messages.HelpText()

        elif isinstance(msg, messages.Version):
            vercheck = self.check_version(msg)
            if vercheck is not None:
                answer = messages.Error("failed: %s" % vercheck)
                self.log(answer)

        elif isinstance(msg, messages.Mode):
            # send the answer in current mode
            force_mode = self.mode

            # but all new messages are in the new mode.
            modeset = self.set_mode(msg)
            if modeset:
                answer = messages.Error("failed setting mode: %s" % modeset)
                self.log(answer)

        elif isinstance(msg, messages.Login):
            if not self.user:
                self.user = msg.name
                self.source = msg.source
                self.log("logged in: %s from %s" % (self.user, self.source),
                         level=logging.DEBUG)

                answer = messages.Welcome("hi %s!" % self.user, CFG.name)

            else:
                answer = messages.Error("tried logging in again")
                self.log(answer)

        # login check
        if not self.user:
            answer = messages.Error("not logged in")
            self.log(answer)

        else:
            # we have a login!
            # TODO: actual permission checking
            # user: {vmname, vmname, ...} access and management
            if isinstance(msg, messages.List):
                self.log("providing machine list",
                         level=logging.DEBUG)

                # the config stores machineid -> (config, class)
                answer = messages.MachineList(
                    [(vm_id, (cls.__name__, cfg.name))
                     for vm_id, (cfg, cls) in CFG.machines.items()]
                )

            elif isinstance(msg, messages.Select):
                self.log("selected machine \x1b[1m%s\x1b[m" % (msg.name),
                         level=logging.DEBUG)
                cfg, machinecls = CFG.machines[msg.name]

                # important: make copy of the config
                # to allow non-global modifications.
                cfg = copy.copy(cfg)

                # select a free ssh port
                free_port = self.falk.register_free_port(cfg.ssh_host)

                if free_port is not None:
                    cfg.ssh_port = free_port
                else:
                    raise RuntimeError(
                        "no free port found for %s" % msg.name)

                # create new container and handle, then store it
                handle_id = self.falk.create_handle(machinecls(cfg))
                self.current_machine = handle_id
                self.running[handle_id] = msg.name

                answer = messages.RunID(handle_id)

            elif isinstance(msg, messages.Status):
                self.log("returning machine status..", level=logging.DEBUG)
                machine = self.get_machine(msg.run_id)
                status = await machine.status()
                status["run_id"] = self.get_machine_id(msg.run_id)
                answer = messages.VMStatus(**status)

            elif isinstance(msg, messages.Prepare):
                self.log("preparing machine..", level=logging.DEBUG)
                await self.get_machine(msg.run_id).prepare(msg.manage)

            elif isinstance(msg, messages.Launch):
                self.log("launching machine..", level=logging.DEBUG)
                await self.get_machine(msg.run_id).launch()

            elif isinstance(msg, messages.Terminate):
                self.log("terminating machine..", level=logging.DEBUG)
                await self.get_machine(msg.run_id).terminate()

            elif isinstance(msg, messages.Cleanup):
                self.log("cleaning up machine..", level=logging.DEBUG)
                await self.get_machine(msg.run_id).cleanup()

                # and remove the handle.
                handle_id = self.get_machine_id(msg.run_id)
                self.falk.delete_handle(handle_id)
                self.current_machine = None
                del self.running[handle_id]

            elif isinstance(msg, messages.Exit):
                self.close()

            elif isinstance(msg, messages.ShutdownWait):
                self.log(f"waiting {msg.timeout}s for machine shutdown..",
                         level=logging.DEBUG)
                success = await self.get_machine(msg.run_id).\
                          wait_for_shutdown(msg.timeout)
                if not success:
                    answer = messages.Error("shutdown-wait timed out")

        self.log("answer: %s" % answer, level=logging.DEBUG)
        return answer, force_mode
