"""
LXD containers.

https://linuxcontainers.org/lxd/
"""

from __future__ import annotations

import asyncio
import logging
import uuid
import shlex
import subprocess
import time
import typing
import yaml

from . import Container, ContainerConfigFile

if typing.TYPE_CHECKING:
    from typing import Any


class LXD(Container):
    """
    Represents a LXD container.
    """

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self._container_id: str | None = None
        self._manage = False

    @classmethod
    def dynamic_ssh_config(cls) -> bool:
        return True

    @classmethod
    def config(cls, machine_id, cfgdata, cfgpath):
        cfg = ContainerConfigFile(machine_id, cfgdata, cfgpath)

        cfg.base_image = cfgdata["base_image"]

        return cfg

    @staticmethod
    async def _run(
        cmd: str,
        output: bool = False,
        shell: bool = False,
    ) -> str | asyncio.subprocess.Process:

        print(f"[lxd] $ {cmd}")
        stdout = subprocess.PIPE if output else None

        if shell:
            proc = await asyncio.create_subprocess_shell(cmd, stdout=stdout)
        else:
            proc = await asyncio.create_subprocess_exec(*shlex.split(cmd), stdout=stdout)

        out, err = await proc.communicate()

        if output:
            return out.decode()
        else:
            return proc

    async def _rinc(
        self,
        cmd: str,
        output: bool = False,
        shell: bool = False,
    ) -> str | asyncio.subprocess.Process:
        if self._container_id is None:
            raise Exception("container id not running")

        if shell:
            return await self._run(f"lxc exec {self._container_id} -- bash -c {shlex.quote(cmd)}", output=output)
        else:
            return await self._run(f"lxc exec {self._container_id} -- {cmd}", output=output)

    async def _lxc_info(self) -> dict[str, Any]:
        out = await self._run(f"lxc info {self._container_id}", output=True)
        info = yaml.safe_load(out)
        return info

    async def _get_ip(
        self,
        want_v4: bool = True, want_v6: bool = False,
        timeout: float = 10.0,
        retry_delay: float = 0.5,
    ) -> tuple[str | None, str | None]:
        """
        queries the ip address for the container.
        it's fetched from the lxd agent.
        """

        ipv4: str | None = None
        ipv6: str | None = None

        attempt = 0
        start_time = time.time()
        end_time = start_time + timeout

        while True:
            if (ipv4 or not want_v4) and (ipv6 or not want_v6):
                break
            attempt += 1
            attempt_time = time.time()

            if attempt_time >= end_time:
                raise TimeoutError(f"couldn't fetch lxd container ip after {attempt_time - start_time:.02f}s in {attempt} attempts")

            cont = False
            info = await self._lxc_info()
            ips = info["Resources"]["Network usage"]["eth0"]["IP addresses"]

            inetv4 = ips.get("inet")
            if inetv4:
                ipv4_candidate, scope = inetv4.split(maxsplit=1)
                if scope == "(global)":
                    # remove the /netsize
                    ipv4 = ipv4_candidate.split("/", maxsplit=1)[0]
                    cont = True

            inetv6 = ips.get("inet6")
            if inetv6:
                ipv6_candidate, scope = inetv6.split(maxsplit=1)
                # TODO: maybe using the link ipv6 is also fine
                if scope == "(global)":
                    # remove the /netsize
                    ipv6 = ipv6_candidate.split("/", maxsplit=1)[0]
                    cont = True

            if cont:
                continue
            else:
                # TODO event-based waiting for container ip
                await asyncio.sleep(min(retry_delay, end_time - attempt_time))

        return ipv4, ipv6

    async def prepare(self, manage: bool = False) -> None:
        """
        Prepare the container image, or rather
        """
        self._manage = manage

        out = await self._run("lxc version", output=True)
        lxc_version = yaml.safe_load(out)
        self.lxd_client_version = lxc_version["Client version"]

    async def launch(self):
        logging.debug("[lxd] launching container")

        self._container_id = (self.cfg.base_image.replace('/', '-').replace(':', '-')
                             + "-" + str(uuid.uuid4()))

        cmd = f"lxc launch {self.cfg.base_image} {self._container_id}"
        if not self._manage:
            # remove after stop
            cmd += " --ephemeral"

        proc = await self._run(cmd)
        if proc.returncode != 0:
            raise Exception("failed to start lxd container")

    async def connection_info(self) -> dict[str, Any]:
        """ Return infos about how to connect to the container """

        # fetch the container's ip
        if self.ssh_host == "__dynamic__":
            if not (await self.is_running()):
                raise Exception("failed getting ip for container - container not running")

            await self._rinc("systemctl is-system-running --wait")

            logging.debug("[lxd] fetching container ip...")
            ipv4, ipv6 = await self._get_ip()
            ssh_host = ipv4
            # TODO: ipv6 link local or global connection?
            logging.debug("[lxd] got container ip: %s", ssh_host)
        else:
            ssh_host = self.ssh_host

        return {
            "ssh_user": self.ssh_user,
            "ssh_host": ssh_host,
            "ssh_port": self.ssh_port,
            "ssh_known_host_key": self.ssh_known_host_key,
        }

    async def is_running(self) -> bool:
        if not self._container_id:
            return False

        info = await self._lxc_info()
        return "running" == info["Status"].lower()

    async def wait_for_shutdown(self, timeout=60):
        if not self._container_id:
            return

        # TODO doesn't seem to be supported yet except for active polling?
        # but lxc stop already waits for the stop anyway!

        return True

    async def terminate(self):
        if not self._container_id:
            return

        # we don't run poweroff since that's up to Chantal's `cleanup`

        await asyncio.wait_for(
            self._run(f"lxc stop --timeout 20 {self._container_id}"),
            timeout=25,
        )

        if self._manage:
            # export the container filesystem as image
            await asyncio.wait_for(
                self._run(f"lxc publish {self._container_id} --alias {self.cfg.base_image} --compression none --reuse"),
                timeout=600,
            )

            await asyncio.wait_for(
                self._run(f"lxc rm {self._container_id}"),
                timeout=60
            )
        self._container_id = None

    async def cleanup(self):
        # when we stop an ephemeral container it's removed anyway
        if not self._container_id:
            return

        await asyncio.wait_for(
            self._run(f"lxc rm --force {self._container_id}"),
            timeout=60
        )
