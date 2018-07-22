"""
Code for creating and interfacing with Chantal instances.
"""

import asyncio
import logging
from pathlib import Path
import subprocess

from .util import INF, SSHKnownHostFile, AsyncWith
from .process import Process, SSHProcess, ProcessFailed, ProcTimeoutError


class Chantal(AsyncWith):
    """
    Virtual machine instance, with ssh login data.
    For a proper clean-up, call cleanup() or use with 'with'.

    # TODO: different connection methods (e.g. agent, non-ssh commands)
    """
    def __init__(self, machine, loop=None):
        self.machine = machine
        self.loop = loop or asyncio.get_event_loop()
        self.ssh_worked = self.loop.create_future()

    def can_connect(self):
        """ return if the vm ssh connection was successful once. """
        if self.ssh_worked.done():
            return self.ssh_worked.result()

        return False

    async def create(self):
        """ create and prepare the machine """
        await self.machine.prepare()
        await self.machine.launch()

    async def upload(self, local_path, remote_folder=".", timeout=10):
        """
        Uploads the file or directory from local_path to
        remote_folder (default: ~).
        """

        with SSHKnownHostFile(self.machine.ssh_host,
                              self.machine.ssh_port,
                              self.machine.ssh_known_host_key) as hostfile:
            command = [
                "scp",
                "-P", str(self.machine.ssh_port),
                "-q",
            ] + hostfile.get_options() + [
                "-r",
                str(local_path),
                self.machine.ssh_user + "@" +
                self.machine.ssh_host + ":" +
                str(remote_folder),
            ]

            async with Process(command) as proc:
                ret = await proc.wait_for(timeout)

                if ret != 0:
                    raise ProcessFailed(ret, "scp upload failed")

    async def download(self, remote_path, local_folder, timeout=10):
        """
        Downloads the file or directory from remote_path to local_folder.
        Warning: Contains no safeguards regarding filesize.
        Clever arguments for remote_path or local_folder might
        allow break-outs.
        """

        with SSHKnownHostFile(self.machine.ssh_host,
                              self.machine.ssh_port,
                              self.machine.ssh_known_host_key) as hostfile:
            command = [
                "scp", "-q",
                "-P", str(self.machine.ssh_port),
            ] + hostfile.get_options() + [
                "-r",
                self.machine.ssh_user + "@" + self.machine.ssh_host + ":" + remote_path,
                local_folder,
            ]

            async with Process(command) as proc:
                ret = await proc.wait_for(timeout)

                if ret != 0:
                    raise ProcessFailed(ret, "scp down failed")

    def exec_remote(self, remote_command,
                    timeout=INF, silence_timeout=INF,
                    must_succeed=True):
        """
        Runs the command via ssh, returns an Process handle.
        """

        return SSHProcess(remote_command,
                          self.machine.ssh_user, self.machine.ssh_host,
                          self.machine.ssh_port, self.machine.ssh_known_host_key,
                          timeout=timeout,
                          silence_timeout=silence_timeout,
                          must_succeed=must_succeed)

    async def run_command(self, remote_command,
                          timeout=INF, silence_timeout=INF,
                          must_succeed=True):
        """
        Raises subprocess.TimeoutExpired if the process has not terminated
        within 'timeout' seconds, or if it has not produced any output in
        'silence_timeout' seconds.
        """

        async with self.exec_remote(remote_command,
                                    timeout, silence_timeout,
                                    must_succeed) as proc:

            # ignore output, but this handles the timeouts.
            async for _, _ in proc.output():
                pass

            return await proc.wait()

    async def cleanup(self):
        """
        Waits for the VM to finish and cleans up.
        """
        try:
            if self.can_connect():
                await self.run_command(('sudo', 'poweroff'), timeout=10,
                                       must_succeed=False)
        except subprocess.TimeoutExpired:
            raise RuntimeError("VM shutdown timeout")
        finally:
            try:
                await self.machine.terminate()
                await self.machine.cleanup()
            except subprocess.SubprocessError:
                logging.warning("[chantal] failed telling falk about VM "
                                "teardown, but he'll do that on its own.")

    async def __aenter__(self):
        await self.create()
        return self

    async def __aexit__(self, exc, value, traceback):
        del exc, traceback  # unused
        try:
            await self.cleanup()
        except Exception as new_exc:
            # the cleanup failed, throw the exception from the old one
            raise new_exc from value

    async def wait_for_connection(self, timeout=60, retry_delay=0.5,
                                  try_timeout=10):
        """
        Wait until the vm can be controlled via ssh
        """

        # TODO: support contacting chantal through
        #       plain socket and not only ssh
        #       and allow preinstallations of chantal
        #       -> SSHChantal, ...
        try:
            await self.machine.wait_for_ssh_port(timeout,
                                                 retry_delay, try_timeout)
        except ProcTimeoutError:
            self.ssh_worked.set_result(False)
            raise

        self.ssh_worked.set_result(True)

    async def install(self, timeout=10):
        """
        Install chantal on the VM
        """

        # TODO: allow to skip chantal installation
        kevindir = Path(__file__)
        await self.upload(kevindir.parent.parent / "chantal",
                          timeout=timeout)

    def run(self, job):
        """
        execute chantal in the VM.
        return a state object, use its .output() function to get
        an async iterator.

        TODO: optionally, launch Docker in the VM
        """

        return self.exec_remote(
            ("python3", "-u", "-m", "chantal",
             "--clone", job.build.clone_url,
             "--checkout", job.build.commit_hash,
             "--desc-file", job.build.project.cfg.job_desc_file,
             job.name),
            timeout=job.build.project.cfg.job_timeout,
            silence_timeout=job.build.project.cfg.job_silence_timeout,
        )
