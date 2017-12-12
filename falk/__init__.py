"""
Falk module properties
"""

from collections import defaultdict

from .config import CFG


class Falk:
    """
    Global state storage for this falk daemon.
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
        self.used_ports[machine.ssh_host].remove(machine.ssh_port)

        del self.running[machine_id]

    def get_machine(self, machine_id):
        """
        return the handle for machine with given id.
        """
        return self.running[machine_id]
