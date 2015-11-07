"""
Code for creating and interfacing with Chantal instances.
"""

import os
import shlex
import socket
import subprocess
import sys
import time

from .config import CFG
from .util import popen_iterate, INF


class Chantal:
    """
    Virtual machine instance, with ssh login data.
    For a proper clean-up, call cleanup() or use with 'with'.
    """
    def __init__(self):
        print("creating overlay image for new VM instance")

        self.running_image = str(CFG.vm_overlay_image)

        # TODO: modularity for other than qemu vms.
        command = [
            "qemu-img",
            "create",
            "-o",
            "backing_file=" + str(CFG.vm_base_image) + ",backing_fmt=qcow2",
            "-f", "qcow2",
            self.running_image,
        ]
        if subprocess.call(command) != 0:
            raise RuntimeError("could not create overlay image")

        print("Launching VM")
        command = []
        for part in shlex.split(CFG.vm_command):
            part = part.replace("IMAGENAME", self.running_image)
            part = part.replace("SSHPORT", str(CFG.vm_ssh_port))
            command.append(part)

        self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
        self.process.stdin.close()

        self.ssh_host = "localhost"
        self.ssh_port = CFG.vm_ssh_port
        self.ssh_user = CFG.vm_image_username

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

                if sock.connect_ex((self.ssh_host, self.ssh_port)) == 0:
                    sock.close()
                    raw_acquired = True
                    print("\x1b[32;5mopen\x1b[m!")
                    continue
                else:
                    print("\x1b[31;5mclosed\x1b[m!")

            else:
                print("testing for ssh service on port... ", end="")
                sys.stdout.flush()
                command = [
                    "ssh",
                    self.ssh_user + "@" + self.ssh_host,
                    "-p", str(self.ssh_port),
                    "true",
                ]
                if subprocess.call(command) == 0:
                    print("\x1b[32;5;1msuccess\x1b[m!")
                    break
                else:
                    print("\x1b[31;5;1mfailed\x1b[m!")

            if time.time() > endtime:
                print("\x1b[31mTIMEOUT\x1b[m")
                raise RuntimeError("timeout while waiting for SSH port")

    def upload(self, local_path, remote_folder="."):
        """
        Uploads the file or directory from local_path to
        remote_folder (default: ~).
        """
        command = [
            "scp",
            "-P", str(self.ssh_port),
            "-r",
            "-q",
            local_path,
            self.ssh_user + "@" + self.ssh_host + ":" + remote_folder
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
        command = [
            "scp",
            "-P", str(self.ssh_port),
            "-r",
            self.ssh_user + "@" + self.ssh_host + ":" + remote_path,
            local_folder
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

        command = [
            "ssh",
            self.ssh_user + "@" + self.ssh_host,
            "-p", str(self.ssh_port),
            "--",
        ]
        command.extend(remote_command)
        ssh_connection = subprocess.Popen(
            command,
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        yield from popen_iterate(ssh_connection, timeout, silence_timeout)

    def is_running(self):
        """
        returns True if the subprocess is still running.
        """
        return self.process.poll() is None

    def cleanup(self):
        """
        Waits for the VM to finish and cleans up.
        """
        try:
            self.run_command('sudo', 'init', '0', timeout=10)
        except subprocess.TimeoutExpired:
            raise RuntimeError("VM shutdown timeout") from None
        finally:
            self.process.kill()
            self.process.wait()
            print("Deleting the old VM image...")
            os.unlink(self.running_image)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, exc_tb):
        del exc_type, exc_tb  # unused

        try:
            self.cleanup()
        except Exception as new_exc:
            raise new_exc from exc
