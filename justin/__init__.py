"""
Justin module properties
"""

import asyncio
import logging
import shutil
import os
from collections import defaultdict

from .config import CFG
from .protocol import JustinProto


class Justin:
    """
    Global state storage for this justin daemon.
    """

    def __init__(self):
        # next used handle_id to identify a running container
        self.handle_id = 0

        # next free connection id to identify incoming control connections
        self.connection_id = 0

        # handle_id -> Container instance
        self.running = dict()

        # contains all used ssh ports
        # hostname -> used ports
        self.used_ports = defaultdict(set)

    def prepare_socket(self):
        try:
            os.unlink(CFG.control_socket)
        except OSError:
            if os.path.exists(CFG.control_socket):
                raise
            else:
                sockdir = os.path.dirname(CFG.control_socket)
                if not os.path.exists(sockdir):
                    try:
                        logging.info("creating socket directory '%s'", sockdir)
                        os.makedirs(sockdir, exist_ok=True)
                    except PermissionError as exc:
                        raise exc from None

    async def run(self):
        logging.warning("listening on '%s'...", CFG.control_socket)

        loop = asyncio.get_running_loop()
        proto_tasks = set()

        def create_proto():
            """ creates the asyncio protocol instance """
            proto = JustinProto(self)

            # create message "worker" task
            proto_task = loop.create_task(proto.process_messages())
            proto_tasks.add(proto_task)

            proto_task.add_done_callback(
                lambda fut: proto_tasks.remove(proto_task))

            return proto

        server = await loop.create_unix_server(create_proto, CFG.control_socket)

        if CFG.control_socket_group:
            # this only works if the current user is a member of the
            # target group!
            shutil.chown(CFG.control_socket, None, CFG.control_socket_group)

        if CFG.control_socket_permissions:
            mode = int(CFG.control_socket_permissions, 8)
            os.chmod(CFG.control_socket, mode)

        try:
            await server.serve_forever()
        except asyncio.CancelledError:
            logging.info("exiting...")
            logging.warning("served %d connections", self.handle_id)

            for proto_task in proto_tasks:
                proto_task.cancel()

            await asyncio.gather(*proto_tasks,
                                 return_exceptions=True)

            raise

    def register_free_port(self, hostname):
        """
        Return a free ssh port.
        `port` is (lower, upper), this function returns the next
        available port in that range.

        if no free port can be found, returns None
        """

        lower, upper = CFG.ssh_port_range

        ret = None

        current = lower
        while True:
            if current in self.used_ports[hostname]:
                current += 1
                if current > upper:
                    raise RuntimeError("no free port found")
            else:
                # TODO: test if the socket is in use by other process?
                ret = current
                break

        self.used_ports[hostname].add(ret)

        return ret

    def get_connection_id(self):
        """ return the next free connection id """
        ret = self.connection_id
        self.connection_id += 1
        return ret

    def create_handle(self, new_machine):
        """
        create a new machine handle
        """

        new_handle = self.handle_id
        self.handle_id += 1
        self.running[new_handle] = new_machine

        return new_handle

    def delete_handle(self, machine_id):
        """
        remove the machine handle with given id.
        """
        machine = self.running[machine_id]

        if not machine.dynamic_ssh_config():
            self.used_ports[machine.ssh_host].remove(machine.ssh_port)

        del self.running[machine_id]

    def get_machine(self, machine_id):
        """
        return the handle for machine with given id.
        """
        return self.running[machine_id]
