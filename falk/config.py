"""
Falk daemon config parsing
"""

from configparser import ConfigParser
import re

from .vm import CONTAINERS

class Config:
    def __init__(self):
        self.control_socket = None
        self.name = None

        # machinename -> machineconfig
        # config created by Container.config()
        self.machines = dict()

    def load(self, filename, shell=False):
        cfg = ConfigParser()
        cfg.read(filename)

        try:
            falkcfg = cfg["falk"]

            self.name = falkcfg["name"]
            self.control_socket = falkcfg["control_socket"]

            self.control_socket_permissions = (
                falkcfg.get("control_socket_permissions"))

            self.control_socket_group = (
                falkcfg.get("control_socket_group"))

            # ssh ports may be a range or a single port
            ssh_port_range = falkcfg["vm_ports"]
            mat = re.match(r"\[(\d+),(\d+)\]", ssh_port_range)
            if mat:
                # port range
                lower, upper = int(mat.group(1)), int(mat.group(2))
                if not lower < upper:
                    raise ValueError("invalid port range (>): [%d,%d]" % (
                        lower, upper))
                self.ssh_port_range = lower, upper
            else:
                raise ValueError("vm_ports malformed, should be =[from,to]")

            # further config ideas:
            # max parallel vms, memory usage checking

        except KeyError as exc:
            print("\x1b[31mConfig file is missing entry: %s\x1b[m" % (exc))
            exit(1)

        if not shell:
            self.load_machines(cfg)

        self.verify()

    def load_machines(self, cfg):
        for machinename, machinecfg in cfg.items():
            if machinename in ("falk", "DEFAULT"):
                # is for the main config above.
                continue
            elif machinename in self.machines:
                raise ValueError("Machine %s specified more than once" % (
                    machinename))

            if "type" not in machinecfg:
                raise KeyError("Machine %s has no type=" % (machinename))

            machineclassname = machinecfg["type"]
            try:
                machineclass = CONTAINERS[machineclassname]
            except KeyError:
                raise ValueError("Unknown Machine type %s" % (
                    machineclassname)) from None

            # each machine type requests different config options,
            # these are parsed here.
            machineconfig = machineclass.config(machinename, machinecfg)
            self.machines[machinename] = (machineconfig, machineclass)

    def verify(self):
        """ Verifies the validity of the loaded config attributes """
        # TODO
        pass


# global falk configuration instance
CFG = Config()
