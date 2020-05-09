"""
Podman containers.

https://podman.io/
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
        self.running_image = None
        self.container_id = None

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

    async def launch(self):
        logging.debug("[podman] launching container with ssh port %d", self.ssh_port)

        self.running_image = (self.cfg.base_image.replace('/', '-').replace(':', '-')
                              + "-" + str(uuid.uuid4()))

        command = []
        for part in shlex.split(self.cfg.command):
            part = part.replace("{BASE_IMAGE}", str(self.cfg.base_image))
            part = part.replace("{SSHPORT}", str(self.ssh_port))
            part = part.replace("{IMAGENAME}", str(self.running_image))
            command.append(part)

        logging.debug(f"[podman] $ {' '.join(command)}")
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None
        )
        process.stdin.close()

        # either the podman run fails,
        # or it echoes the container hash and then exits with 0
        loop = asyncio.get_event_loop()
        exit_wait_task = loop.create_task(process.wait())
        readline_task = loop.create_task(process.stdout.readline())
        done, pending = await asyncio.wait({exit_wait_task, readline_task},
                                           return_when=asyncio.FIRST_COMPLETED)
        if readline_task in pending:
            readline_task.cancel()
            if not exit_wait_task in done:
                raise RuntimeError("process did not exit, but also print no line.")
            raise Exception("no container id was provided by podman, "
                            "pls investigate launch command")
        else:
            self.container_id = readline_task.result().strip().decode()
            logging.debug("[podman] spawned container with hash %s" % self.container_id)

        if exit_wait_task in pending:
            ret = await exit_wait_task
        else:
            ret = exit_wait_task.result()

        if ret != 0:
            self.running_image = None
            self.container_id = None

    async def is_running(self):
        if not self.running_image:
            return False

        command = ['podman', 'inspect', '-f', '\'{{.State.Running}}\'', self.running_image]
        logging.debug(f"[podman] $ {' '.join(command)}")
        process = await asyncio.create_subprocess_exec(
            *command,
        )
        out, err = await process.communicate()
        return 'true' in out.decode()

    async def wait_for_shutdown(self, timeout=60):
        if not self.running_image:
            return

        command = ['podman', 'wait', self.running_image]
        logging.debug(f"[podman] $ {' '.join(command)}")
        process = await asyncio.create_subprocess_exec(
            *command,
        )

        try:
            await asyncio.wait_for(process.wait(), timeout)
            return True

        except asyncio.TimeoutError:
            logging.warning("[podman] shutdown wait timed out "
                            f"for container {self.running_image}")
            return False

    async def terminate(self):
        if not self.running_image:
            return

        command = ['podman', 'stop', self.running_image]
        logging.debug(f"[podman] $ {' '.join(command)}")
        process = await asyncio.create_subprocess_exec(
            *command,
        )
        await asyncio.wait_for(process.wait(), timeout=20)

        self.process = None

    async def cleanup(self):
        if not self.running_image:
            return

        command = ["podman", "rm", "-f", self.running_image]
        logging.debug(f"[podman] $ {' '.join(command)}")
        process = await asyncio.create_subprocess_exec(
            *command,
        )
        await asyncio.wait_for(process.wait(), timeout=20)
