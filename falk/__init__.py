"""
Falk module properties
"""

from collections import defaultdict

from .config import CFG


class Falk:
    """
    State storage for this falk daemon.
    """

    def __init__(self):
        # next used handle_id to identify a running container
        self.handle_id = 0

        # handle_id -> Container instance
        self.running = dict()

        # contains all used ssh ports
        # hostname -> used ports
        self.used_ports = defaultdict(lambda: set())

    def get_free_port(self, hostname):
        """
        Return a free ssh port.
        `port` is (lower, upper), this function returns the next
        available port in that range.

        if no free port can be found, returns None

        TODO: check if another process owns the port.
        """

        lower, upper = CFG.ssh_port_range

        current = lower
        while True:
            if current not in self.used_ports[hostname]:
                return current
            else:
                current += 1
                if current > upper:
                    return None

    def create_handle(self, new_machine):
        """
        create a new machine handle
        """
        new_handle = self.handle_id
        self.handle_id += 1

        self.running[new_handle] = new_machine

        # register used ssh port
        if new_machine.ssh_port in self.used_ports[new_machine.ssh_host]:
            raise RuntimeError("tried using occupied ssh port %d on '%s' " % (
                new_machine.ssh_port, new_machine.ssh_host))

        self.used_ports[new_machine.ssh_host].add(new_machine.ssh_port)

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
