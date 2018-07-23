"""
Custom container, just shell scripts are invoked.
"""

import asyncio
import logging
import os
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

    async def prepare(self, manage=False):
        self.manage = manage

        prepare_env = os.environ.copy()
        prepare_env["FALK_MANAGE"] = "true" if self.manage else ""

        command = shlex.split(self.cfg.prepare)
        proc = await asyncio.create_subprocess_exec(*command, env=prepare_env)

        try:
            ret = await asyncio.wait_for(proc.wait(), timeout=60)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("timeout waiting for "
                               "container preparation") from exc

        if ret != 0:
            raise RuntimeError(f"could not prepare container: returned {ret}")

    async def launch(self):
        logging.debug("Launching container which shall listen "
                      "on ssh port %d", self.ssh_port)

        launch_env = os.environ.copy()
        launch_env["FALK_SSH_PORT"] = str(self.ssh_port)
        launch_env["FALK_MANAGE"] = "true" if self.manage else ""

        command = []
        for part in shlex.split(self.cfg.launch):
            part = part.replace("{SSHPORT}", str(self.ssh_port))
            command.append(part)

        self.process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.PIPE,
            stdout=None,
            stderr=None,
            env=launch_env
        )
        self.process.stdin.close()

    async def is_running(self):
        if self.process:
            return self.process.returncode is None

        return False

    async def wait_for_shutdown(self, timeout):
        if not self.process:
            return

        try:
            await asyncio.wait_for(self.process.wait(), timeout)
            return True

        except asyncio.TimeoutError:
            logging.warning("shutdown wait timed out.")
            return False

    async def terminate(self):
        if not self.process:
            return

        if self.process.returncode is not None:
            return

        try:
            self.process.terminate()
            await asyncio.wait_for(self.process.wait(), timeout=10)

        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()

        self.process = None

    async def cleanup(self):
        command = shlex.split(self.cfg.cleanup)
        cleanup_env = os.environ.copy()
        cleanup_env["FALK_MANAGE"] = "true" if self.manage else ""

        proc = await asyncio.create_subprocess_exec(*command, env=cleanup_env)

        try:
            ret = await asyncio.wait_for(proc.wait(), timeout=60)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("timeout cleaning up container") from exc

        if ret != 0:
            raise RuntimeError(f"could not clean up container: {ret}")
