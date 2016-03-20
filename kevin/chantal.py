"""
Code for creating and interfacing with Chantal instances.
"""

import asyncio
import logging
from pathlib import Path
import subprocess

from .util import INF, SSHKnownHostFile
from .process import Process, SSHProcess, ProcessFailed, ProcTimeoutError


class Chantal:
    """
    Virtual machine instance, with ssh login data.
    For a proper clean-up, call cleanup() or use with 'with'.

    # TODO: different connection methods (e.g. agent, non-ssh commands)
    """
    def __init__(self, vm):
        self.vm = vm
        self.ssh_worked = asyncio.Future()

    def can_connect(self):
        """ return if the vm ssh connection was successful once. """
        if self.ssh_worked.done():
            return self.ssh_worked.result()
        else:
            return False

    async def create(self):
        """ create and prepare the machine """
        await self.vm.prepare()
        await self.vm.launch()

    async def upload(self, local_path, remote_folder=".", timeout=10):
        """
        Uploads the file or directory from local_path to
        remote_folder (default: ~).
        """

        with SSHKnownHostFile(self.vm.ssh_host,
                              self.vm.ssh_port,
                              self.vm.ssh_key) as hostfile:
            command = [
                "scp",
                "-P", str(self.vm.ssh_port),
                "-q",
            ] + hostfile.get_options() + [
                "-r",
                str(local_path),
                self.vm.ssh_user + "@" +
                self.vm.ssh_host + ":" +
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

        with SSHKnownHostFile(self.vm.ssh_host,
                              self.vm.ssh_port,
                              self.vm.ssh_key) as hostfile:
            command = [
                "scp", "-q",
                "-P", str(self.vm.ssh_port),
            ] + hostfile.get_options() + [
                "-r",
                self.vm.ssh_user + "@" + self.vm.ssh_host + ":" + remote_path,
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
                          self.vm.ssh_user, self.vm.ssh_host,
                          self.vm.ssh_port, self.vm.ssh_key,
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
            async for fd, data in proc.output():
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
                await self.vm.terminate()
                await self.vm.cleanup()
            except subprocess.SubprocessError:
                logging.warn("[chantal] failed telling falk about VM "
                             "teardown, but he'll do that on its own.")

    def __enter__(self):
        raise Exception("use async with!")

    def __exit__(self, exc, value, traceback):
        raise Exception("use async with!")

    async def __aenter__(self):
        await self.create()
        return self

    async def __aexit__(self, exc, value, traceback):
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
            await self.vm.wait_for_ssh_port(timeout,
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
        """

        return self.exec_remote(
            ("python3", "-u", "-m", "chantal",
             job.build.clone_url,
             job.build.commit_hash,
             job.build.project.cfg.job_desc_file),
            timeout=job.build.project.cfg.job_timeout,
            silence_timeout=job.build.project.cfg.job_silence_timeout,
        )
