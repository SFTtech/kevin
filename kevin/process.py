"""
subprocess utility functions and classes.
"""

import asyncio
import asyncio.streams
import logging
import signal
import subprocess
import sys

from .util import INF, SSHKnownHostFile, AsyncWith


class ProcessError(subprocess.SubprocessError):
    """
    Generic process error.
    """
    def __init__(self, msg):
        super().__init__(msg)

    def __str__(self):
        return f"Process failed: {self}"


class LineReadError(ProcessError):
    """
    We could not read the requested amount of lines!
    """
    def __init__(self, msg, cmd):
        super().__init__(msg)
        self.cmd = cmd


class ProcTimeoutError(ProcessError):
    """
    A subprocess can timeout because it took to long to finish or
    no output was received for a given time period.
    This exception is raised and stores whether it just took too long,
    or did not respond in time to provide any new output.
    """
    def __init__(self, cmd, timeout, was_global=False):
        super().__init__("Process timeout")
        self.cmd = cmd
        self.timeout = timeout
        self.was_global = was_global

    def __str__(self):
        return ("Command '%s' timed out after %s seconds" %
                (self.cmd, self.timeout))


class ProcessFailed(ProcessError):
    """
    The executed process returned with a non-0 exit code
    """
    def __init__(self, returncode, cmd):
        super().__init__("Process failed")
        self.returncode = returncode
        self.cmd = cmd

    def __str__(self):
        if self.returncode and self.returncode < 0:
            try:
                return "Process '%s' died with %r." % (
                    self.cmd, signal.Signals(-self.returncode))
            except ValueError:
                return "Process '%s' died with unknown signal %d." % (
                    self.cmd, -self.returncode)
        else:
            return "Process '%s' returned non-zero exit status %d." % (
                self.cmd, self.returncode)


class Process(AsyncWith):
    """
    Contains a running process specified by command.

    You really should use this in `async with Process(...) as proc:`.

    Main feature: interact/communicate with the process multiple times.
    You can repeatedly send data to stdin and fetch the replies.

    chop_lines: buffer for lines
    must_succeed: throw ProcessFailed on non-0 exit
    pipes: True=create pipes to the process, False=reuse your terminal
    loop: the event loop to run this process in
    linebuf_max: maximum size of the buffer for line chopping
    queue_size: maximum number of entries in the output queue
    """

    def __init__(self, command, chop_lines=False, must_succeed=False,
                 pipes=True, loop=None,
                 linebuf_max=(8 * 1024 ** 2), queue_size=1024):

        self.loop = loop or asyncio.get_event_loop()

        self.created = False

        # future to track force-exit exceptions.
        # this contains the exception if the program
        # was killed e.g. because of a timeout.
        self.killed = self.loop.create_future()

        pipe = subprocess.PIPE if pipes else None
        self.capture_data = pipes

        self.proc = self.loop.subprocess_exec(
            lambda: WorkerInteraction(self, chop_lines,
                                      linebuf_max, queue_size),
            *command,
            stdin=pipe, stdout=pipe, stderr=pipe)

        self.args = command
        self.chop_lines = chop_lines
        self.must_succeed = must_succeed

        self.transport = None
        self.protocol = None

        self.exit_callbacks = list()

    async def create(self):
        """ Launch the process """
        if self.created:
            raise Exception("process already created")

        logging.debug("creating process '%s'...", self.args)

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
            raise Exception("can't communicate as process was not yet created")

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
        if not self.transport:
            raise Exception("Process has never run!")

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

        # TODO: if this poll is removed,
        #       the process may be terminated
        #       but the pid is no longer running!??
        if self.transport and self.transport._proc:
            self.transport._proc.poll()

        # check if the process already exited
        ret = self.returncode()

        if ret is not None:
            return ret

        try:
            self.terminate()
            return await asyncio.wait_for(self.wait(), timeout=term_timeout)

        except asyncio.TimeoutError:
            self.kill()
            return await self.wait()

    def send_signal(self, sig):
        """ send a signal to the process """
        self.transport.send_signal(sig)

    def terminate(self):
        """ send sigterm to the process """
        self.transport.terminate()

    def kill(self):
        """ send sigkill to the process """
        self.transport.kill()

    def on_exit(self, callback):
        """
        call the callback when the process exited
        callback() is called.
        """
        if not callable(callback):
            raise ValueError(f"invalid callback: {callback}")

        self.exit_callbacks.append(callback)

    def fire_exit_callbacks(self):
        """
        Called from the protocol when the process exited.
        """
        for callback in self.exit_callbacks:
            callback()

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

    if ssh_known_host_key is None, don't do fingerprint checking!

    timeout: max runtime of the process for an output() call
    silence_timeout: max silence time of the process for an output() call
    chop_lines: search for \n and buffer linewise
    must_succeed: raise an ProcessFailed exception if return value != 0
    pipes: capture output, else reuse the current pty
    options: additional ssh option list
    """

    def __init__(self, command, ssh_user, ssh_host, ssh_port, ssh_known_host_key=None,
                 timeout=INF, silence_timeout=INF, chop_lines=False,
                 must_succeed=False, pipes=True, options=None,
                 loop=None, linebuf_max=(8 * 1024 ** 2), queue_size=1024):

        if not isinstance(command, (list, tuple)):
            raise Exception("invalid command: %r" % (command,))

        # generates a temporary known_hosts file
        # for ssh host key verification.
        self.ssh_hash = SSHKnownHostFile(ssh_host, ssh_port, ssh_known_host_key)
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

        super().__init__(ssh_cmd, chop_lines=chop_lines, loop=loop,
                         linebuf_max=linebuf_max, queue_size=queue_size,
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
        """
        cleanup the ssh connection by removing the ssh known hosts file
        """
        self.ssh_hash.remove()

    async def __aexit__(self, exc, value, traceback):
        self.cleanup()
        await super().__aexit__(exc, value, traceback)


class WorkerInteraction(asyncio.streams.FlowControlMixin,
                        asyncio.SubprocessProtocol):
    """
    Subprocess protocol specialization to allow output line buffering.
    """
    def __init__(self, process, chop_lines, linebuf_max, queue_size=INF):
        super().__init__()
        self.process = process
        self.buf = bytearray()
        self.transport = None
        self.stdin = None
        self.linebuf_max = linebuf_max
        self.queue = asyncio.Queue(maxsize=(
            queue_size if queue_size < INF else 0))
        self.chop_lines = chop_lines

    def connection_made(self, transport):
        self.transport = transport

        stdin_transport = self.transport.get_pipe_transport(0)
        self.stdin = asyncio.StreamWriter(stdin_transport,
                                          protocol=self,
                                          reader=None,
                                          loop=asyncio.get_event_loop())

    def pipe_connection_lost(self, fdnr, exc):
        """
        the given fd is no longer connected.
        """
        del exc
        self.transport.get_pipe_transport(fdnr).close()

    def process_exited(self):
        # process exit happens after all the pipes were lost.

        logging.debug("Process %s exited", self.process)

        # send out remaining data to queue
        if self.buf:
            self.enqueue_data((1, bytes(self.buf)))
            del self.buf[:]

        # mark the end-of-stream
        self.enqueue_data(StopIteration)
        self.transport.close()
        self.process.fire_exit_callbacks()

    async def write(self, data):
        """
        Wait until data was written to the process stdin.
        """
        self.stdin.write(data)
        # TODO: really drain?
        await self.stdin.drain()

    def pipe_data_received(self, fdnr, data):
        if len(self.buf) + len(data) > self.linebuf_max:
            raise ProcessError("too much data")

        if fdnr == 1:
            # add data to buffer
            self.buf.extend(data)

            if self.chop_lines:
                npos = self.buf.rfind(b"\n")

                if npos < 0:
                    return

                lines = self.buf[:npos].split(b"\n")

                for line in lines:
                    self.enqueue_data((fdnr, bytes(line)))

                del self.buf[:(npos+1)]

            # no line chopping
            else:
                self.enqueue_data((fdnr, bytes(self.buf)))
                del self.buf[:]
        else:
            # non-stdout data:
            self.enqueue_data((fdnr, data))

    def enqueue_data(self, data):
        """
        Add data so it can be sent to the process.
        """
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
                lambda: self.timeout(was_global=True))

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
        """
        Yields tuples of (fd, data) where fd is one of
        {stdout_fileno, stderr_fileno}, and data is the bytes object
        that was written to that stream (may be a line if requested).

        If the process times out or some error occurs,
        this function will raise the appropriate exception.
        """

        while True:
            # cancel the previous line timeout
            if self.output_timeout < INF:
                if self.line_timer:
                    self.line_timer.cancel()

                # and set the new one
                self.line_timer = self.loop.call_later(
                    self.output_timeout,
                    lambda: self.timeout(was_global=False))

            # send data to stdin
            if self.data:
                await self.process.protocol.write(self.data)
                self.data = None

            # we emitted enough lines
            if self.lines_emitted >= self.linecount:
                # stop the iteration as there were enough lines
                self.error_check()
                return

            # now, either the process exits,
            # there's an exception (process will be killed)
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

                    for future_pending in pending:
                        try:
                            await future_pending
                        except asyncio.CancelledError:
                            pass

                    # kill the process before throwing the error!
                    await self.process.pwn()
                    raise future.exception()

                # fetch output from the process
                entry = future.result()

                # The result is StopIteration to indicate that the process
                # output stream ended.
                if entry is StopIteration:
                    # no more data, so stop iterating
                    self.error_check()
                    return

                fdnr, data = entry

                # only count stdout lines
                if fdnr == 1:
                    self.lines_emitted += 1

                yield fdnr, data

    def error_check(self):
        """
        Check if the number of read lines is expected.

        This is called either when the process exited,
        or when enough lines were read.
        """

        # cancel running timers as we're terminating anyway
        if self.line_timer:
            self.line_timer.cancel()

        if self.overall_timer:
            self.overall_timer.cancel()

        retcode = self.process.returncode()

        if self.process.must_succeed:
            if retcode is not None and retcode != 0:
                raise ProcessFailed(retcode, self.process.args)

        # check if we received enough lines
        if self.linecount < INF and self.lines_emitted < self.linecount:
            # received 0 lines:
            if self.lines_emitted == 0:
                raise ProcessError("process did not provide any stdout lines")
            else:
                raise LineReadError("could only read %d of %d line%s" % (
                    self.lines_emitted, self.linecount,
                    "s" if self.linecount > 1 else ""), self.process.args)


async def test_coro(output_timeout, timeout):
    """
    Run an async process with Python to test its functionality.

    This should kill the process because of line timeout.
    """
    import tempfile

    prog = tempfile.NamedTemporaryFile()
    prog.write(b"\n".join([
        b'import signal',
        b'import sys, time',
        b'signal.signal(signal.SIGTERM, lambda a, b: print("haha term"))',
        b'print("hai!")',
        b'for i in range(int(input("> "))):',
        b'    print(i)',
        b'    time.sleep(0.5 + i * 0.1)',
    ]))
    prog.flush()

    async with Process([sys.executable, '-u', prog.name],
                       chop_lines=True) as proc:

        try:
            logging.info("proc: %s", proc.args)

            async for fdn, line in proc.communicate(
                    b"10\n", output_timeout=output_timeout,
                    timeout=timeout,
                    linecount=4):

                logging.info("1st: %d %s", fdn, line)

            async for fdn, line in proc.communicate(
                    output_timeout=output_timeout,
                    timeout=timeout,
                    linecount=2):

                logging.info("2nd: %d %s", fdn, line)

            async for fdn, line in proc.communicate(output_timeout=1,
                                                    timeout=2):

                logging.info("last: %d %s", fdn, line)


        except ProcTimeoutError as exc:
            logging.info("timeout %s: %s" % (
                "global" if exc.was_global else "line",
                exc))

        logging.info("ret=%d" % (await proc.wait()))


def test():
    """
    Test the async Process creation
    """
    loop = asyncio.get_event_loop()
    loop.set_debug(True)

    from .util import log_setup

    log_setup(2)

    try:
        task = loop.create_task(test_coro(output_timeout=2, timeout=5))
        loop.run_until_complete(task)
    except KeyboardInterrupt:
        logging.info("\nexiting...")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        logging.info("fail: %s", exc)

    loop.stop()
    loop.run_forever()
    loop.close()

if __name__ == "__main__":
    test()
