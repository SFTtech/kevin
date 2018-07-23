"""
Wraps a QEMU virtual machine.
"""

import asyncio
import logging
import os
from pathlib import Path
import shlex
import subprocess

from . import Container, ContainerConfig


class QEMU(Container):
    """
    Represents a qemu virtual machine.
    """

    def __init__(self, cfg):
        super().__init__(cfg)
        self.manage = False
        self.process = None
        self.running_image = None

    @classmethod
    def config(cls, machine_id, cfgdata, cfgpath):
        cfg = ContainerConfig(machine_id, cfgdata, cfgpath)

        base_img = Path(cfgdata["base_image"])
        if not base_img.is_absolute():
            base_img = cfgpath / base_img
        cfg.base_image = base_img.absolute()

        overlay_img = Path(cfgdata["overlay_image"])
        if not overlay_img.is_absolute():
            overlay_img = cfgpath / overlay_img
        cfg.overlay_image = overlay_img.absolute()

        cfg.command = cfgdata["command"]

        if not cfg.base_image.is_file():
            raise FileNotFoundError("base image: %s" % cfg.base_image)

        return cfg

    async def prepare(self, manage=False):
        self.manage = manage

        if not self.manage:
            # create a temporary runimage
            idx = 0
            while True:
                tmpimage = Path(str(self.cfg.overlay_image) + "_%02d" % idx)
                if not tmpimage.is_file():
                    break
                idx += 1

            self.running_image = tmpimage

            command = [
                "qemu-img", "create",
                "-o", "backing_file=" + str(self.cfg.base_image),
                "-f", "qcow2",
                str(self.running_image),
            ]

            proc = await asyncio.create_subprocess_exec(*command)

            try:
                ret = await asyncio.wait_for(proc.wait(), timeout=60)
            except asyncio.TimeoutError as exc:
                raise RuntimeError("timeout when creating "
                                   "overlay image") from exc

            if ret != 0:
                raise RuntimeError(f"could not create overlay image: "
                                   f"qemu-img returned {ret}")

        else:
            # TODO: even in management mode, create a cow image,
            #       but in the end merge it back into a new image and
            #       perform an atomic rename(2) in order to atomically
            #       replace the VM.
            #       currently, builds that were triggered while the VM
            #       is being managed may use a corrupted image.

            # TODO: disallow multiple management connections at once.

            logging.info("QEMU VM launching in management mode!")
            # to manage, use the base image to run
            self.running_image = str(self.cfg.base_image)

    async def launch(self):
        if self.running_image is None:
            raise RuntimeError("runimage was not prepared!")

        logging.debug("Launching VM which shall listen "
                      "on ssh port %d", self.ssh_port)

        command = []
        for part in shlex.split(self.cfg.command):
            part = part.replace("{IMAGENAME}", str(self.running_image))
            part = part.replace("{SSHPORT}", str(self.ssh_port))
            command.append(part)

        self.process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.PIPE,
            stdout=None,
            stderr=None
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
        if not self.manage and self.running_image is not None:
            try:
                os.unlink(str(self.running_image))
            except FileNotFoundError:
                pass
