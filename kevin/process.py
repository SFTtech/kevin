"""
subprocess utility functions and classes.
"""

import asyncio, asyncio.streams
import fcntl
import functools
import os
import selectors
import subprocess
import sys
import time

from .util import INF, SSHKnownHostFile


class ProcTimeoutError(subprocess.TimeoutExpired):
    """
    A subprocess can timeout because it took to long to finish or
    no output was received for a given time period.
    This exception is raised and stores whether it just took too long,
    or did not respond in time to provide any new output.
    """
    def __init__(self, cmd, timeout, was_global=False):
        super().__init__(cmd, timeout)
        self.was_global = was_global


class LineReadError(subprocess.SubprocessError):
    """
    We could not read the requested amount of lines!
    """
    def __init__(self, msg, cmd):
        super().__init__(msg)
        self.cmd = cmd


class ProcessFailed(subprocess.CalledProcessError):
    """
    The executed process returned with a non-0 exit code
    """
    def __init__(self, retcode, args):
        super().__init__(retcode, args)


class ProcessError(subprocess.SubprocessError):
    """
    The process did something unexpected.
    """
    def __init__(self, msg, cmd):
        super().__init__(msg)
        self.cmd = cmd


class Process:
    """
    Contains a running process specified by command.

    Main feature: interact/communicate with the process multiple times.
    You can repeatedly send data to stdin and fetch the replies.

    chop_lines: buffer for lines
    must_succeed: throw ProcessFailed on non-0 exit
    pipes: True=create pipes to the process, False=reuse your terminal
    loop: the event loop to run this process in
    linebuf_max: maximum size of the buffer for line chopping
    line_cache: maximum number of entries in the line queue
    """

    def __init__(self, command, chop_lines=False, must_succeed=False,
                 pipes=True, loop=None,
                 linebuf_max=(8 * 1024 ** 2), line_cache=128):

        self.loop = loop or asyncio.get_event_loop()

        self.created = False
        self.killed = asyncio.Future()

        pipe = subprocess.PIPE if pipes else None
        self.capture_data = pipes

        self.proc = self.loop.subprocess_exec(
            lambda: WorkerInteraction(self, chop_lines,
                                      linebuf_max, line_cache),
            *command,
            stdin=pipe, stdout=pipe, stderr=pipe)

        self.args = command
        self.chop_lines = chop_lines
        self.must_succeed = must_succeed

        self.transport = None
        self.protocol = None

    async def create(self):
        """ Launch the process """
        if self.created:
            raise Exception("process already created")

        self.transport, self.protocol = await self.proc
        self.created = True

    def communicate(self, data=None, timeout=INF,
                    output_timeout=INF, linecount=INF):
        """
        Interacts with the process io streams.

        You get an asynchronous iterator for `async for`:
        Use it as `async for (fd, data) in proc.communicate(...):`.

        Can feed data to stdin.

        if linecount is finite:
            The iterator will return the number of requested lines.
            Output from stderr is returned as well, but not counted.

        else:
            Runs until the process terminates or times out.
            returns chunks as they come from the process.
        """

        if not self.created:
            raise Exception("process.create() not yet called")

        if self.killed.done():
            raise Exception("process was already killed "
                            "and no output is waiting")

        if not self.capture_data:
            raise Exception("pipes=False, but you wanted to communicate()")

        if linecount < INF and not self.chop_lines:
            raise Exception("can't do line counting when it's disabled "
                            "upon process creation (chop_lines)")

        return ProcessIterator(self, self.loop, data, timeout,
                               output_timeout, linecount)

    def readline(self, data=None, timeout=INF):
        """ send data to stdin and read one line of response data """
        return self.communicate(data, output_timeout=timeout, linecount=1)

    def returncode(self):
        """ get the exit code, or None if still running """
        return self.transport.get_returncode()

    async def wait(self):
        """ wait for exit and return the exit code. """
        return await self.transport._wait()

    async def wait_for(self, timeout=None):
        """
        Wait a limited time for exit and return the exit code.
        raise ProcTimeoutError if it took too long.
        """
        try:
            return await asyncio.wait_for(self.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise ProcTimeoutError(self.args, timeout)

    async def pwn(self, term_timeout=5):
        """
        make sure the process is dead (terminate, kill, wait)
        return the exit code.
        """

        ret = self.returncode()

        if ret is not None:
            return ret

        try:
            self.terminate()
            return await asyncio.wait_for(self.wait(), timeout=term_timeout)

        except asyncio.TimeoutError:
            self.kill()
            return await self.wait()

    def send_signal(self, signal):
        """ send a signal to the process """
        self.transport.send_signal(signal)

    def terminate(self):
        """ send sigterm to the process """
        self.transport.terminate()

    def kill(self):
        """ send sigkill to the process """
        self.transport.kill()

    def __enter__(self):
        raise Exception("use async with!")

    def __exit__(self, exc, value, traceback):
        raise Exception("use async with!")

    async def __aenter__(self):
        await self.create()
        return self

    async def __aexit__(self, exc, value, traceback):
        await self.pwn()


class SSHProcess(Process):
    """
    Status handle for a in-guest process execution via SSH.

    Provides a crafted ssh known hosts file to specify the exact key
    allowed for the connection.

    Use it as an async context manager or be sure to call cleanup()
    after using, else the tempfile will remain in /tmp.

    if ssh_key is None, don't do fingerprint checking!

    timeout: max runtime of the process for an output() call
    silence_timeout: max silence time of the process for an output() call
    chop_lines: search for \n and buffer linewise
    must_succeed: raise an ProcessFailed exception if return value != 0
    pipes: capture output, else reuse the current pty
    options: additional ssh option list
    """

    def __init__(self, command, ssh_user, ssh_host, ssh_port, ssh_key=None,
                 timeout=INF, silence_timeout=INF, chop_lines=False,
                 must_succeed=False, pipes=True, options=None,
                 linebuf_max=(8 * 1024 ** 2), line_cache=128):

        if not isinstance(command, (list, tuple)):
            raise Exception("invalid command: %r" % (command,))

        self.ssh_hash = SSHKnownHostFile(ssh_host, ssh_port, ssh_key)
        self.ssh_hash.create()

        if options is None:
            options = []

        ssh_cmd = [
            "ssh", "-q",
        ] + self.ssh_hash.get_options() + options + [
            "-p", str(ssh_port),
            ssh_user + "@" + ssh_host,
            "--",
        ] + list(command)

        super().__init__(ssh_cmd, chop_lines=chop_lines,
                         linebuf_max=linebuf_max, line_cache=line_cache,
                         must_succeed=must_succeed, pipes=pipes)

        self.timeout = timeout
        self.silence_timeout = silence_timeout

    def output(self, linecount=INF, timeout=None, silence_timeout=None):
        """
        return an async iterator to get the process output
        use as `async for fd, data in bla.output(...):`

        linecount: number of lines to wait for
        timeout: maximum duration of iteration
        silence_timeout: maximum silence time of iteration
        """

        o_timeout = timeout or self.timeout
        o_output_timeout = silence_timeout or self.silence_timeout

        return self.communicate(data=None,
                                timeout=o_timeout,
                                output_timeout=o_output_timeout,
                                linecount=linecount)

    def cleanup(self):
        self.ssh_hash.remove()

    async def __aexit__(self, exc, value, traceback):
        self.cleanup()
        await super().__aexit__(exc, value, traceback)


class WorkerInteraction(asyncio.streams.FlowControlMixin,
                        asyncio.SubprocessProtocol):
    def __init__(self, process, chop_lines, linebuf_max, line_cache=INF):
        super().__init__()
        self.process = process
        self.buf = bytearray()
        self.transport = None
        self.stdin = None
        self.linebuf_max = linebuf_max
        self.queue = asyncio.Queue(maxsize=(
            line_cache if line_cache < INF else 0))
        self.chop_lines = chop_lines

    def connection_made(self, transport):
        self.transport = transport

        stdin_transport = self.transport.get_pipe_transport(0)
        self.stdin = asyncio.StreamWriter(stdin_transport,
                                          protocol=self,
                                          reader=None,
                                          loop=asyncio.get_event_loop())

    def pipe_connection_lost(self, fd, exc):
        # the given fd is no longer connected.
        pass

    def process_exited(self):
        # process exit happens after all the pipes were lost.

        # send out remaining data
        if self.buf:
            self.enqueue_data((1, bytes(self.buf)))
            del self.buf[:]

        # mark the end-of-stream
        self.enqueue_data(StopIteration)

    async def write(self, data):
        self.stdin.write(data)
        # TODO: really drain?
        await self.stdin.drain()

    def pipe_data_received(self, fd, data):
        if len(self.buf) + len(data) > self.linebuf_max:
            raise subprocess.SubprocessError(
                "too much data")

        if fd == 1:
            # add data to buffer
            self.buf.extend(data)

            if self.chop_lines:
                npos = self.buf.rfind(b"\n")

                if npos < 0:
                    return

                lines = self.buf[:npos].split(b"\n")

                for line in lines:
                    self.enqueue_data((fd, bytes(line)))

                del self.buf[:(npos+1)]

            # no line chopping
            else:
                self.enqueue_data((fd, bytes(self.buf)))
                del self.buf[:]
        else:
            # non-stdout data:
            self.enqueue_data((fd, data))

    def enqueue_data(self, data):
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull as exc:
            if not self.process.killed.done():
                self.process.killed.set_exception(exc)


class ProcessIterator:
    """
    Aasynchronous iterator for the process output.
    Interacts with its process and provides output.

    use like `async for (fd, data) in ProcessIterator(...):`
    """

    def __init__(self, process, loop, data=None, run_timeout=INF,
                 output_timeout=INF, linecount=INF):
        """
        data: will be fed to stdin of process.
        timeout: the iteration will only take this amount of time.
        output_timeout: one iteration stel may only take this long.
        linecount: the number of lines we want to receive.
        """
        self.process = process
        self.loop = loop

        self.data = data
        self.run_timeout = run_timeout
        self.output_timeout = output_timeout
        self.linecount = linecount

        self.lines_emitted = 0
        self.line_timer = None
        self.overall_timer = None

        if self.run_timeout < INF:
            # set the global timer
            self.overall_timer = self.loop.call_later(
                self.run_timeout,
                functools.partial(self.timeout, was_global=True))

    def timeout(self, was_global=False):
        """
        Called when the process times out.
        line_output: it was the line-timeout that triggered.
        """

        if not self.process.killed.done():
            self.process.killed.set_exception(ProcTimeoutError(
                self.process.args,
                self.run_timeout if was_global else self.output_timeout,
                was_global,
            ))

    async def __aiter__(self):
        return self

    async def __anext__(self):
        """
        Returns a tuple of (fd, data) where fd is one of
        {stdout_fileno, stderr_fileno}, and data is the bytes object
        that was written to that stream (may be a line if requested).
        """

        # cancel the previous line timeout
        if self.output_timeout < INF:
            if self.line_timer:
                self.line_timer.cancel()

            # and set the new one
            self.line_timer = self.loop.call_later(
                self.output_timeout,
                functools.partial(self.timeout, was_global=False))

        # send data to stdin
        if self.data:
            await self.process.protocol.write(self.data)
            self.data = None

        # we sent enough data
        if self.lines_emitted >= self.linecount:
            await self.stop_iter(enough=True)

        # now, either the process exits,
        # there's an exception (process killed)
        # or the queue gives us the next data item.
        # wait for the first of those events.
        done, pending = await asyncio.wait(
            [self.process.protocol.queue.get(), self.process.killed],
            return_when=asyncio.FIRST_COMPLETED)

        # at least one of them is done now:
        for future in done:
            # if something failed, cancel the pending futures
            # and raise the exception
            # this happens e.g. for a timeout.
            if future.exception():
                for future_pending in pending:
                    future_pending.cancel()

                # kill the process before throwing the error!
                await self.process.pwn()
                raise future.exception()

            # fetch output from the process
            entry = future.result()

            # it can be stopiteration to indicate the last data chunk
            # as the process exited on its own.
            if entry == StopIteration:
                if not self.process.killed.done():
                    self.process.killed.set_result(entry)

                    # raise the stop iteration
                    await self.stop_iter(enough=False)

            fd, data = entry

            # only count stdout lines
            if fd == 1:
                self.lines_emitted += 1

            return fd, data

        raise Exception("internal fail: no future was done!")

    async def stop_iter(self, enough=False):
        """
        Raise StopAsyncIteration or another exception.
        Check if the number of read lines is expected.

        This is called either when the process exited,
        or when enough lines were read.

        enough: True=reason was enough lines, False=reason was proc exit.
        """

        # cancel running timers as we're terminating anyway
        if self.line_timer:
            self.line_timer.cancel()

        if self.overall_timer:
            self.overall_timer.cancel()

        retcode = self.process.returncode()

        if self.process.must_succeed:
            if retcode is not None:
                if retcode != 0:
                    raise ProcessFailed(retcode, self.process.args)

        # check if we received enough lines
        if self.linecount < INF and self.lines_emitted < self.linecount:
            # received 0 lines:
            if self.lines_emitted == 0:
                raise ProcessError(
                    "process did not provide any stdout lines",
                    self.process.args)
            else:
                raise LineReadError("could only read %d of %d line%s" % (
                    self.lines_emitted, self.linecount,
                    "s" if self.linecount > 1 else ""), self.process.args)

        raise StopAsyncIteration()


async def test_coro(loop, output_timeout, timeout):
    import tempfile

    f = tempfile.NamedTemporaryFile()
    f.write(b"\n".join([
        b'import signal',
        b'import sys, time',
        b'signal.signal(signal.SIGTERM, lambda a, b: print("haha term"))',
        b'print("hai!")',
        b'for i in range(int(input("> "))):',
        b'    print(i)',
        b'    time.sleep(0.5 + i * 0.1)',
    ]))
    f.flush()

    async with Process([sys.executable, '-u', f.name],
                       chop_lines=True) as proc:

        try:
            print("proc: %s" % proc.args)

            async for fd, line in proc.communicate(
                b"10\n", output_timeout=output_timeout, timeout=timeout,
                linecount=4):

                print("1st: %d %s" % (fd, line))

            async for fd, line in proc.communicate(
                output_timeout=output_timeout, timeout=timeout,
                linecount=2):

                print("2nd: %d %s" % (fd, line))

            async for fd, line in proc.communicate(output_timeout=1,
                                                   timeout=2):

                print("last: %d %s" % (fd, line))


        except ProcTimeoutError as exc:
            print("timeout %s: %s" % ("global" if exc.was_global else "line",
                                      exc))

        print("ret=%d" % (await proc.wait()))


def test():
    loop = asyncio.get_event_loop()
    loop.set_debug(True)

    try:
        task = loop.create_task(test_coro(loop, output_timeout=2, timeout=5))
        loop.run_until_complete(task)
    except KeyboardInterrupt:
        print("\nexiting...")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print("fail: %s" % exc)

    loop.stop()
    loop.run_forever()
    loop.close()

if __name__ == "__main__":
    import argparse
    test()
