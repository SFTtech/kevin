"""
Docker containers.

https://www.docker.com/
"""

from asyncio import base_subprocess
from pathlib import Path
from requests import HTTPError
import asyncio
import concurrent
import logging
import io
import os
import shlex
import signal
import subprocess
import struct
import tarfile
import threading
import uuid

from .base import Container, ContainerConfig, DockerContainerConfig
from kevin.util import INF
from kevin.process import Process, WorkerInteraction
import docker


class _DockerProcess:
    """
    A class representing a remote docker container process and implementing
    the subprocess.Popen class interface.
    """

    def __init__(self, loop, container, cmd, protocol, waiter, on_exit,
                 height=None, width=None):

        self._container = container
        self._dockerc_base = container.cfg.dockerc_base
        self._container_id = container.container.id
        self._cmd = cmd
        self._protocol = protocol
        self._docker_waiter = waiter
        self._on_exit = on_exit
        self._height = height or 600
        self._width = width or 800
        self._returncode = None
        self._loop = loop
        self._exit_event = threading.Event()

        self._init_event = threading.Event()
        asyncio.ensure_future(
            self._loop.run_in_executor(None, self._do_init),
            loop=self._loop)
        self._init_event.wait()

    def _do_init(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._stdin_r, self._stdin_w = os.pipe()
            self._stdout_r, self._stdout_w = os.pipe()
            self._stderr_r, self._stderr_w = os.pipe()
            self._stdin_f = os.fdopen(self._stdin_w, mode="wb")
            self._stdout_f = os.fdopen(self._stdout_r, mode="rb")
            self._stderr_f = os.fdopen(self._stderr_r, mode="rb")
            self._streams = {
                1: self._stdout_w,
                2: self._stderr_w,
            }
            loop.add_reader(self._stdin_r, self._process_writer)

            self._exec_res = self._dockerc_base.exec_create(
                self._container_id, self._cmd, tty=False,
                stdin=True, stdout=True, stderr=True
            )
            self._exec_id = self._exec_res["Id"]
            # FIXME: exec_resize request throw an HTTP 500 error
            # self._dockerc_base.exec_resize(
            #     self._exec_id, height=self._height, width=self._width)
            iosocket = self._dockerc_base.exec_start(
                self._exec_id, detach=False, tty=False,
                stream=True, socket=True
            )
            self._socket = iosocket._sock

            self._inspect_res = self._dockerc_base.exec_inspect(self._exec_id)
            self._pid = self._inspect_res["Pid"]
        except:
            # This is only useful for debugging
            logging.exception("")
            loop.close()
            self._tear_down()
            raise
        finally:
            self._init_event.set()

        loop.run_until_complete(self._process_read())
        self._exit_event.set()
        loop.close()

    def _inspect(self):
        self._inspect_res = self._dockerc_base.exec_inspect(self._exec_id)
        self._pid = self._inspect_res["Pid"]
        if not self._inspect_res["Running"]:
            self._returncode = self._inspect_res["ExitCode"]

    async def _process_read(self):
        data = self._socket.recv(4096)
        fd = -1
        frames = {
            1: b"",
            2: b""
        }
        lenght = None
        try:
            while self._returncode is None or data:
                # Read output and error streams
                if fd > 0:
                    missing = lenght - len(frames[fd])
                    frames[fd] += data[:missing]
                    data = data[missing:]
                    if len(frames[fd]) == lenght:
                        os.write(self._streams[fd], frames[fd])
                        frames[fd] = b""
                        fd = - 1
                        lenght = None
                elif len(data) >= 8:
                    fd, lenght = struct.unpack_from(b">BxxxI", data[:8])
                    data = data[8:]

                if not data:
                    await asyncio.sleep(0.100)
                    chunk = self._socket.recv(4096)
                    data += chunk

                # Inspect container
                self._inspect()
                if not self._docker_waiter.done():
                    self._loop.call_soon_threadsafe(
                        self._docker_waiter.set_result, None)
        finally:
            self._tear_down()

    def _tear_down(self):
        self._loop.call_soon_threadsafe(
            self._protocol.pipe_connection_lost, 1, None)
        self._loop.call_soon_threadsafe(
            self._protocol.pipe_connection_lost, 2, None)
        self._loop.call_soon_threadsafe(self._protocol.process_exited)
        self._loop.call_soon_threadsafe(self._on_exit, self._returncode)
        for fd in (self._stdout_w, self._stderr_w, self._stdin_r):
            self._loop.call_soon_threadsafe(
                self._loop.call_later, 1, os.close, fd)
        try:
            self._socket.close()
        except OSError:
            pass

    def _process_writer(self):
        data = os.read(self._stdin_r, 4096)
        if data:
            self._socket.send(data)

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        """
        Not used by asyncio and not tested here.
        """
        # I really don't trust myself with this untested code
        # TODO: test this function and remove the following line
        raise NotImplementedError
        self._exit_event.wait(timeout)
        return self._returncode

    def communicate(input=None, timeout=None):
        """
        Not used by asyncio and not tested here.
        """
        # I really don't trust myself with this untested code
        # TODO: test this function and remove the following line
        raise NotImplementedError
        if input is not None:
            self._stdin_f.write(input)
        self.wait(timeout=timeout)
        done = threading.Event()
        out = b""
        err = b""

        def _do_comunicate(out, err, done):
            async def _ado_communicate(out, err, done):
                out += await self._protocol.stdout.read()
                err += await self._protocol.stderr.read()
                done.set()
            asyncio.ensure_future(_ado_communicate(out, err, done))

        self._loop.call_soon_threadsafe(_do_comunicate(out, err, done))
        done.wait()
        return out, err

    def send_signal(self, signal):
        asyncio.wait(asyncio.ensure_future(
            self._container.send_signal(signal, self._pid),
            loop=self._loop), loop=self._loop)

    def terminate(self):
        self.send_signal(signal.SIGTERM)

    def kill(self):
        self.send_signal(signal.SIGKILL)

    @property
    def args(self):
        return self._cmd

    @property
    def stdin(self):
        return self._stdin_f

    @property
    def stdout(self):
        return self._stdout_f

    @property
    def stderr(self):
        return self._stderr_f

    @property
    def pid(self):
        return self._pid

    @property
    def returncode(self):
        return self._returncode


class _DockerProcessTransport(base_subprocess.BaseSubprocessTransport):

    def __init__(self, loop, protocol, args, shell,
                 stdin, stdout, stderr, bufsize, dockerc_base, container,
                 waiter=None, extra=None, **kwargs):
        self.dockerc_base = dockerc_base
        self.container = container
        self._docker_waiter = waiter
        self._docker_loop = loop
        super(_DockerProcessTransport, self).__init__(
            loop, protocol, args, shell,
            stdin, stdout, stderr, bufsize,
            waiter=waiter, extra=extra, **kwargs)

    def _start(self, args, shell, stdin, stdout,
               stderr, bufsize, **kwargs):
        self._proc = _DockerProcess(
            self._docker_loop, self.container, args,
            self._protocol, self._docker_waiter, self._process_exited
        )


class DockerProcess(Process):

    def __init__(self, container, *args, **kwds):
        self.container = container
        super(DockerProcess, self).__init__(*args, **kwds)

    async def _prepare(self, chop_lines, linebuf_max, queue_size, command):
        loop = asyncio.get_event_loop()
        protocol = WorkerInteraction(self, chop_lines, linebuf_max, queue_size)
        waiter = loop.create_future()
        transport = _DockerProcessTransport(
            loop=loop, protocol=protocol, args=command, shell=False,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=4096, waiter=waiter,
            dockerc_base=self.container.cfg.dockerc_base, container=self.container)
        try:
            await waiter
        except Exception:
            logging.exception("")
            transport.close()
            await transport._wait()
            raise
        return transport, protocol


class Docker(Container):
    """
    Represents a Docker container.
    """

    def __init__(self, cfg):
        super().__init__(cfg)
        self.manage = False
        self.container = None
        self.socket = None

    @classmethod
    def config(cls, machine_id, cfgdata, cfgpath):
        cfg = DockerContainerConfig(machine_id, cfgdata, cfgpath)
        return cfg

    def set_config(self, cfg):
        super(Docker, self).set_config(cfg)
        self.cfg.dockerc = docker.DockerClient(base_url=cfg.socket_uri)
        self.cfg.dockerc_base = self.cfg.dockerc.api

    async def prepare(self, manage=False):
        logging.info("preparing instance...")
        if self.container is not None:
            raise RuntimeError("runimage is already prepared!")
        self.manage = manage

        def _do_prepare():
            if not self.manage:
                if self.cfg.context_path is None:
                    raise ValueError(
                        f"Cannot build image '{self.cfg.image_name}': "
                        f"missing docker context path")
                image, logs = self.cfg.dockerc.images.build(
                    path=str(self.cfg.context_path),
                    dockerfile=self.cfg.dockerfile,
                    rm=True,
                    tag=self.cfg.image_name
                )
                for log in logs:
                    print("Build '{}': {}".format(self.cfg.image_name, log))
            logging.info("instance image name : {}".format(self.cfg.image_name))

            # Create a container with a shell process waiting for its stdin
            container_name = "{}-{}".format(
                self.cfg.name, str(uuid.uuid4())[:8])
            self.container = self.cfg.dockerc.containers.create(
                name=container_name,
                image=self.cfg.image_name,
                command="/bin/bash",
                detach=False,
                auto_remove=True,
                stdin_open=True,
                tty=False,
            )

        await asyncio.get_event_loop().run_in_executor(None, _do_prepare)
        logging.info("instance name : {}".format(self.container.name))

    async def execute(self, cmd,
                      timeout=INF, silence_timeout=INF,
                      must_succeed=True):
        """
        Runs the command via ssh, returns an Process handle.
        """
        if not await self.is_running():
            raise RuntimeError(
                "Cannot execute '{}': container is not running".format(cmd))

        return DockerProcess(self, cmd)

    async def upload(self, local_path, remote_folder="/", timeout=10):
        """
        Uploads the file or directory from local_path to
        remote_folder (default: ~).
        """
        if not await self.is_running():
            raise RuntimeError(
                f"Cannot upload '{local_path}': "
                f"container is not running")
        with io.BytesIO() as f:
            def _do_upload(local_path, remote_folder):
                local_path = Path(local_path)
                tar = tarfile.open(fileobj=f, mode="w:gz")
                tar.add(name=str(local_path), arcname=local_path.name)
                tar.close()
                f.seek(0)
                self.cfg.dockerc_base.put_archive(
                    self.container.id, remote_folder, f.read())

            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, _do_upload, local_path, remote_folder),
                timeout=timeout)

    async def download(self, remote_path, local_folder, timeout=10):
        """
        Downloads the file or directory from remote_path to local_folder.
        Warning: Contains no safeguards regarding filesize.
        Clever arguments for remote_path or local_folder might
        allow break-outs.
        """
        if not await self.is_running():
            raise RuntimeError(
                f"Cannot download '{remote_path}': "
                f"container is not running")
        with io.BytesIO() as f:
            def _do_download():
                chunks, stat = self.cfg.dockerc_base.get_archive(
                    self.container.id, remote_path)
                for chunk in chunks:
                    f.write(chunk)
                f.seek(0)
                tar = tarfile.open(fileobj=f, mode="r:*")
                tar.extract(Path(remote_path).name, path=local_folder)
                tar.close()

            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, _do_download),
                timeout=timeout)

    async def launch(self):
        if self.container is None:
            raise RuntimeError("runimage was not prepared!")

        await asyncio.get_event_loop().run_in_executor(
            None, self.container.start)
        iosocket = await asyncio.get_event_loop().run_in_executor(
            None, self.cfg.dockerc_base.attach_socket, self.container.id,
            dict(stdin=True, stdout=True, stderr=True, stream=True), False)
        self.socket = iosocket._sock

    async def is_running(self):
        if self.container is not None:
            self.container = await asyncio.get_event_loop().run_in_executor(
                None, self.cfg.dockerc.containers.get, self.container.id)
            return self.container.status == "running"
        else:
            return False

    async def send_signal(self, signal, pid):
        if self.container is not None:
            await asyncio.get_event_loop().run_in_executor(
                None, self.socket.send,
                "kill -{} {}".format(signal, pid).encode('utf-8'))

    async def terminate(self):
        if self.container is not None:
            # Send the shell "exit" command
            await asyncio.get_event_loop().run_in_executor(
                None, self.socket.send, b"exit\n")
            await self.wait_for_shutdown()

    async def wait_for_shutdown(self, timeout=60):
        """
        sleep for a maximum of `timeout` until the container terminates.
        """
        if self.container is not None:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self.cfg.dockerc_base.wait, self.container.id,
                    timeout
                )
            except HTTPError:
                pass

    async def cleanup(self):
        if self.container is not None:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self.cfg.dockerc_base.remove_container,
                    self.container.id)
            except HTTPError:
                pass
            self.container = None

        self.cfg.dockerc_base.close()


# Tests
# Usage:
#   python -m falk.vm.docker
async def main():
    import filecmp

    cfgpath = Path(__file__).parent / "tests" / "ubuntu_ping"
    cfgdata = {
        "type": "docker",
        "image_name": "ubuntu_ping",
        "context_path": str(cfgpath)
    }
    machine_id = "zorro"
    container = Docker(Docker.config(machine_id, cfgdata, cfgpath))
    # Below if manage is False, you have to provide a docker context path in the
    # cfgdata so that falk can build the docker image for you
    await container.prepare(manage=False)
    await container.launch()
    print("Container {} is running {}".format(
        container,
        await container.is_running()
    ))
    await execute(container, "ping -c 3 localhost")
    await container.upload(__file__, "/tmp")
    await execute(container, "cat /tmp/{}".format(Path(__file__).name))
    await container.download("/tmp/docker.py", "/tmp")
    print("upload/download test: {}".format(
        "OK" if filecmp.cmp(__file__, "/tmp/docker.py") else "KO"))
    print("terminate...")
    await container.terminate()
    print("cleanup...")
    await container.cleanup()


async def execute(container, cmd):
    print("execute '{}'...".format(cmd))
    p = await container.execute(cmd)
    await p.create()
    async for fd, data in p.communicate():
        print(data.decode('utf-8').strip('\n'))
    await p.wait()
    print("execute '{}' returned: {}".format(cmd, p.returncode()))

if __name__ == "__main__":
    #import warnings
    #warnings.simplefilter('always', ResourceWarning)
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    loop.slow_callback_duration = 0.5
    loop.run_until_complete(main())
