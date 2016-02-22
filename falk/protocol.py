"""
Falk communication protocol.
"""

import asyncio
import copy
import traceback

from . import messages
from .config import CFG
from .messages import ProtoType
from .vm import CONTAINERS


# number of connections, is tracked for logging purposes
CONN_COUNT = 0

# message protocol version
VERSION = 0


class FalkProto(asyncio.Protocol):
    """
    Communication protocol for falks control socket.
    """

    # default: user-friendly text mode.
    DEFAULT_MODE = ProtoType.text

    def __init__(self, falk):
        self.falk = falk

        self.buf = bytearray()
        self.maxbuf = (8 * 1024 * 1024)  # 8 MiB max buffer

        self.user = None
        self.ip = None

        # containers running in this session
        # contains {handle_id: vmname, ...}
        self.running = dict()

        # set by the Select message
        self.current_machine = None

    def connection_made(self, transport):
        global CONN_COUNT
        self.conn_id = CONN_COUNT
        CONN_COUNT += 1

        self.log("new client connected")
        self.transport = transport

        self.mode = self.DEFAULT_MODE

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
            try:
                if line.strip():
                    self.control_message(
                        messages.Message.construct(line, self.mode)
                    )

            except BaseException as exc:
                self.log("\x1b[31;1merror processing request:\x1b[m")
                traceback.print_exc()
                self.send(messages.Error(repr(exc)))

    def connection_lost(self, exc):
        self.log("lost connection")

        # before this protocol dies, kill all associated machines.
        for handle_id, name in self.running.items():
            container = self.get_machine(handle_id)

            self.log("killing container %s..." % name)
            container.terminate()
            container.cleanup()

        if exc:
            self.log("connection loss reason: %s" % repr(exc))

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

    def log(self, msg):
        """
        logs something for this connection
        """

        print("[%3d] %s" % (self.conn_id, msg))

    def check_version(self, msg):
        """
        Checks if the given version is compatible with our one.
        """

        if not msg.version == VERSION:
            return "unmatching versions: you=%d != us=%d" % (msg.version,
                                                             VERSION)

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

    def control_message(self, msg):
        """
        Parse and handle falk control messages.
        This defines falks behavior!
        """

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
                self.log(err)
            else:
                answer = messages.OK()

        elif isinstance(msg, messages.Mode):
            # send the answer in current mode
            force_mode = self.mode

            # but all new messages are in the new mode.
            modeset = self.set_mode(msg)
            if modeset:
                answer = messages.Error("failed setting mode: %s" % modeset)
                self.log(answer)

            else:
                answer = messages.OK()

        elif isinstance(msg, messages.Login):
            if not self.user:
                self.user = msg.name
                self.source = msg.source
                self.log("logged in: %s from %s" % (self.user, self.source))

                answer = messages.Welcome("hi %s!" % self.user, CFG.name)

            else:
                answer = messages.Error("tried logging in again")
                self.log(answer)

        # login check
        if not self.user:
            err = messages.Error("not logged in")
            self.log(err)

        else:
            # we have a login!
            # TODO: actual permission checking
            # user: {vmname, vmname, ...} access and management
            if isinstance(msg, messages.List):

                # the config stores machineid -> (config, class)
                answer = messages.MachineList(
                    [(vm_id, (cls.__name__, cfg.name))
                     for vm_id, (cfg, cls) in CFG.machines.items()]
                )

            elif isinstance(msg, messages.Select):
                cfg, machinecls = CFG.machines[msg.name]

                # important: make copy of the config
                # to allow non-global modifications.
                cfg = copy.copy(cfg)

                # select a free ssh port
                free_port = self.falk.get_free_port(cfg.ssh_host)

                if free_port is not None:
                    cfg.ssh_port = free_port
                else:
                    raise RuntimeError("no free port found for %s" % msg.name)

                # create new container and handle, then store it
                handle_id = self.falk.create_handle(machinecls(cfg))
                self.current_machine = handle_id
                self.running[handle_id] = msg.name

                answer = messages.RunID(handle_id)

            elif isinstance(msg, messages.Status):
                machine = self.get_machine(msg.run_id)
                status = machine.status()
                status["run_id"] = self.get_machine_id(msg.run_id)
                answer = messages.VMStatus(**status)

            elif isinstance(msg, messages.Prepare):
                self.get_machine(msg.run_id).prepare(msg.manage)

            elif isinstance(msg, messages.Launch):
                # the execution happens fork'd off
                self.get_machine(msg.run_id).launch()

            elif isinstance(msg, messages.Terminate):
                self.get_machine(msg.run_id).terminate()

            elif isinstance(msg, messages.Cleanup):
                self.get_machine(msg.run_id).cleanup()

                # and remove the handle.
                handle_id = self.get_machine_id(msg.run_id)
                self.falk.delete_handle(handle_id)
                self.current_machine = None
                del self.running[handle_id]

            elif isinstance(msg, messages.Exit):
                self.close()

        if answer:
            self.send(answer, force_mode)
