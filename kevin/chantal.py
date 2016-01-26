"""
Code for creating and interfacing with Chantal instances.
"""

from pathlib import Path
import socket
import subprocess
import sys
import time

from .util import INF, SSHKnownHostFile
from .process import Process


class Chantal:
    """
    Virtual machine instance, with ssh login data.
    For a proper clean-up, call cleanup() or use with 'with'.

    # TODO: different connection methods (e.g. agent, non-ssh commands)
    """
    def __init__(self, vm):
        self.vm = vm
        self.vm.prepare()
        self.vm.launch()

    def wait_for_ssh_port(self, timeout=30, retry_interval=0.2):
        """
        Loops until the SSH port is open.
        raises RuntimeError on timeout.
        """
        raw_acquired = False
        endtime = time.time() + timeout
        while True:
            time.sleep(retry_interval)

            if not raw_acquired:
                print("testing for ssh port... ", end="")
                sys.stdout.flush()
                sock = socket.socket()

                if sock.connect_ex((self.vm.ssh_host,
                                    self.vm.ssh_port)) == 0:
                    sock.close()
                    raw_acquired = True
                    print("\x1b[32;5mopen\x1b[m!")
                    continue
                else:
                    print("\x1b[31;5mclosed\x1b[m!")

            else:
                print("testing for ssh service on port... ", end="")
                sys.stdout.flush()

                with SSHKnownHostFile(self.vm.ssh_host,
                                      self.vm.ssh_port,
                                      self.vm.ssh_key) as hostfile:

                    command = [
                        "ssh",
                    ] + hostfile.get_options() + [
                        "-p", str(self.vm.ssh_port),
                        self.vm.ssh_user + "@" + self.vm.ssh_host,
                        "true",
                    ]

                    if subprocess.call(command) == 0:
                        print("\x1b[32;5;1msuccess\x1b[m!")
                        break
                    else:
                        print("\x1b[31;5;1mfailed\x1b[m!")

            if time.time() > endtime:
                print("\x1b[31mTIMEOUT\x1b[m")
                if raw_acquired:
                    print("TCP connection established, but no SSH.")
                    if self.vm.ssh_key is not None:
                        print(" Are you sure the ssh key is correct?")
                        print(" -> %s" % (self.vm.ssh_key))

                raise RuntimeError("timeout while waiting for SSH port")

    def upload(self, local_path, remote_folder="."):
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
            if subprocess.call(command) != 0:
                raise RuntimeError("SCP failed")

    def download(self, remote_path, local_folder):
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

            if subprocess.call(command) != 0:
                raise RuntimeError("SCP failed")

    def run_command(self, *remote_command, timeout=INF, silence_timeout=INF):
        """
        Runs the command via ssh and yields tuples of (stream id, bytes).

        Raises subprocess.TimeoutExpired if the process has not terminated
        within 'timeout' seconds, or if it has not produced any output in
        'silence_timeout' seconds.

        Raises CalledProcessError if the process has failed.
        """

        with SSHKnownHostFile(self.vm.ssh_host,
                              self.vm.ssh_port,
                              self.vm.ssh_key) as hostfile:

            command = [
                "ssh", "-q"
            ] + hostfile.get_options() + [
                "-p", str(self.vm.ssh_port),
                self.vm.ssh_user + "@" + self.vm.ssh_host,
                "--",
            ]

            # the command to be executed on the host
            command.extend(remote_command)

            # create the process
            ssh_connection = Process(command)

            # yields all the (stream, data)
            yield from ssh_connection.communicate(
                timeout=timeout,
                individual_timeout=silence_timeout,
            )

    def cleanup(self):
        """
        Waits for the VM to finish and cleans up.
        """
        try:
            self.run_command('sudo', 'poweroff', timeout=10)
        except subprocess.TimeoutExpired:
            raise RuntimeError("VM shutdown timeout") from None
        finally:
            self.vm.terminate()
            self.vm.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, exc_tb):
        del exc_type, exc_tb  # unused

        try:
            self.cleanup()
        except Exception as new_exc:
            raise new_exc from exc

    def wait_for_connection(self, timeout=30, retry_delay=0.5):
        """
        wait until chantal can be reached.
        """

        # TODO: support contacting chantal through
        #       plain socket and not only ssh
        #       and allow preinstallations of chantal
        self.wait_for_ssh_port(timeout=30)

    def install(self):
        """
        Install chantal on the VM
        """

        # TODO: allow to skip chantal installation
        kevindir = Path(__file__)
        self.upload(kevindir.parent.parent / "chantal")

    def run(self, job):
        """
        execute chantal in the VM.
        yield all the output.
        """

        chantal_output = self.run_command(
            "python3", "-u", "-m",
            "chantal", job.build.clone_url,
            job.build.commit_hash,
            job.build.project.cfg.job_desc_file,
            timeout=job.build.project.cfg.job_timeout,
            silence_timeout=job.build.project.cfg.job_silence_timeout,
        )
        yield from chantal_output
