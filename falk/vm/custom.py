"""
Custom container, just shell scripts are invoked.
"""

import shlex
import subprocess

from . import Container, ContainerConfig


class Custom(Container):
    """
    Represents a custom virtual machine/container
    launched by custom shell scripts.
    """

    def __init__(self, cfg):
        super().__init__(cfg)
        self.manage = False
        self.process = None

    @classmethod
    def config(cls, machine_id, cfgdata, cfgpath):
        cfg = ContainerConfig(machine_id, cfgdata, cfgpath)

        cfg.prepare = cfgdata["prepare"]
        cfg.launch = cfgdata["launch"]
        cfg.cleanup = cfgdata["cleanup"]

        return cfg

    def prepare(self, manage=False):
        self.manage = manage

        command = shlex.split(self.cfg.prepare)
        if self.manage:
            command.append("--manage")

        if subprocess.call(command) != 0:
            raise RuntimeError("could not prepare container")

    def launch(self):
        command = shlex.split(self.cfg.launch) + [self.ssh_port]

        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        self.process.stdin.close()

    def is_running(self):
        if self.process:
            running = self.process.poll() is None
        else:
            running = False

        return running

    def terminate(self):
        if self.process:
            self.process.kill()
            self.process.wait()

    def cleanup(self):
        command = shlex.split(self.cfg.cleanup)
        if self.manage:
            command.append("--manage")

        if subprocess.call(command) != 0:
            raise RuntimeError("could not clean up container")
