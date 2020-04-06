"""
Podman containers.

https://podman.io//
"""

import asyncio
import logging
import uuid
import shlex
import subprocess

from . import Container, ContainerConfig


class Podman(Container):
    """
    Represents a pdoman container.
    """

    def __init__(self, cfg):
        super().__init__(cfg)
        self.process = None
        self.running_image = None

    @classmethod
    def config(cls, machine_id, cfgdata, cfgpath):
        cfg = ContainerConfig(machine_id, cfgdata, cfgpath)

        cfg.base_image = cfgdata["base_image"]
        cfg.command = cfgdata["command"]

        return cfg

    async def prepare(self, manage=False):
        """
        No need to prepare the container image as we can directly run it
        """
        if manage:
            raise RuntimeError("Docker image cannot be started in management mode")

        self.running_image = self.cfg.base_image + "-" + str(uuid.uuid4())

    async def launch(self):
        logging.debug("Launching podman container which shall listen "
                      "on ssh port %d", self.ssh_port)

        command = []
        for part in shlex.split(self.cfg.command):
            part = part.replace("{BASE_IMAGE}", str(self.cfg.base_image))
            part = part.replace("{SSHPORT}", str(self.ssh_port))
            part = part.replace("{IMAGENAME}", str(self.running_image))
            command.append(part)

        self.process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.PIPE,
            stdout=None,
            stderr=None
        )
        self.process.stdin.close()

    async def is_running(self):
        command = ['podman', 'inspect', '-f', '\'{{.State.Running}}\'', self.running_image]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        out, err = await process.communicate()
        return 'true' in out.decode()

    async def wait_for_shutdown(self, timeout=60):
        if not self.process:
            return

        command = ['podman', 'wait', self.running_image]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.PIPE,
            stdout=None,
            stderr=None
        )
        process.stdin.close()

        try:
            await asyncio.wait_for(process.wait(), timeout)
            return True

        except asyncio.TimeoutError:
            logging.warning("shutdown wait timed out.")
            return False

    async def terminate(self):
        if not self.process:
            return

        command = ['podman', 'stop', self.running_image]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.PIPE,
            stdout=None,
            stderr=None
        )
        process.stdin.close()
        await asyncio.wait_for(process.wait(), timeout=20)

        self.process = None

    async def cleanup(self):
        command = ["podman", "rm", "-f", self.running_image]

        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.PIPE,
            stdout=None,
            stderr=None
        )
        process.stdin.close()
        await asyncio.wait_for(process.wait(), timeout=20)
