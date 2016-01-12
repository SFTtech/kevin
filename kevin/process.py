"""
subprocess utility functions and classes.
"""

import fcntl
import os
import selectors
import subprocess
import time

from .util import INF


class ProcTimeoutError(subprocess.TimeoutExpired):
    """
    A subprocess can timeout because it took to long to finish or
    no output was received for a given time period.
    This exception is raised and stores whether it just took too long,
    or did not respond in time to provide any new output.
    """
    def __init__(self, cmd, timeout, was_global):
        super().__init__(cmd, timeout)
        self.was_global = was_global


class Process:
    """
    subprocess wrapper.
    """

    def __init__(self, command):

        # create subprocess with new fds for each stream.
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # stdout-line-read buffer
        self.buf = bytearray()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.terminate()

    def terminate(self, timeout=INF):
        """
        tries terminnating, then killing the process.

        returns the programs return value.
        """
        if timeout == INF:
            timeout = None

        # try to terminate it.
        self.proc.terminate()

        try:
            return self.proc.wait(timeout)

        # if it doesn't exit, kill it
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait(timeout)

    def communicate(self, data=None, timeout=INF, individual_timeout=INF,
                    kill_timeout=None, linecount=INF,
                    linebuf_max=(8 * 1024 ** 2)):
        """
        Interacts with the process io streams.

        Can feed data to stdin.

        if linecount is finite:
            Runs until at least the requested number of lines
            were yielded from stdout as data.

            Output from stderr is yielded as well, but not counted.

            linebuf_max specifies the maximum amount of data to cache
            to look for lines.

        else:
            Runs until the process terminates or times out.
            the yielded data chunk is just one read call from the pipe.

        Yields tuples of (stream, data) where stream is one of
        {stdout_fileno, stderr_fileno}, and data is the bytes object
        that was written to that stream (may be a line if requested).

        Raises ProcTimeoutError if more than timeout seconds
        pass in total, or more than individual_timeout seconds pass
        during an individual wait.

        Raises CalledProcessError(str, retval) if the process fails.
        proc is guaranteed to be dead and no zombie afterwards.
        """

        start = time.monotonic()

        # data to feed to stdin.
        if data:
            input_feed = bytearray(data)
        else:
            input_feed = bytearray()

        # number of lines read from stdin.
        lines_emitted = 0

        def get_timeout(tell_which=False):
            """
            returns the time until either individual or global timeout
            (whatever is closer).

            returns None if there is no timeout.
            if tell_which is true, return (which_one, timeout),
            where which_one == True if the global timeout was chosen.
                            == False if the individual timeout was selected.
            """
            time_left = (start + timeout) - time.monotonic()

            result = min(
                INF if timeout is INF else time_left,
                individual_timeout
            )

            if result == INF:
                max_time = None
            else:
                max_time = result

            if tell_which:
                if individual_timeout < time_left:
                    return (False, max_time)
                else:
                    return (True, max_time)
            else:
                return max_time

        sel = selectors.DefaultSelector()

        # fd -> usual_fd, event, can_linechop
        pipes = {
            self.proc.stdout.fileno(): (1, selectors.EVENT_READ, True),
            self.proc.stderr.fileno(): (2, selectors.EVENT_READ, False),
        }

        # if stdin should get data, write has to be ready
        if input_feed:
            pipes[self.proc.stdin.fileno()] = (0, selectors.EVENT_WRITE, False)

        # set pipe nonblocking and register it to select()
        for pipe, (_, event, _) in pipes.items():
            flags = fcntl.fcntl(pipe, fcntl.F_GETFL)
            fcntl.fcntl(pipe, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            sel.register(pipe, event, None)

        # keep running while pipes are open
        # and we didn't get enough lines.
        while pipes and lines_emitted < linecount:
            waitmsg_time = time.monotonic()

            # calculate time to wait for ready file descriptors
            was_global, time_left = get_timeout(True)

            # if time_left > 0, wait for that time.
            # if <= 0, don't wait and immediately return ready fds.
            events = sel.select(time_left)

            # if select provided no ready file descriptor:
            if not events:
                # T-T-T-T-T-TIMEOUT!!!!11
                # make sure it's dead
                self.proc.terminate()

                try:
                    self.proc.wait(kill_timeout)

                # still not dead, kill it again.
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(kill_timeout)

                if was_global:
                    overtime_start = start
                else:
                    overtime_start = waitmsg_time

                # if proc could be killed, report the timeout.
                raise ProcTimeoutError(
                    self.proc.args,
                    time.monotonic() - overtime_start,
                    was_global,
                )

            # process all ready filedescriptors
            for event, _ in events:
                while True:
                    try:
                        vfd, action, can_chop = pipes[event.fileobj]
                        if action == selectors.EVENT_READ:
                            data = os.read(event.fd, 16384)

                            # EOF, pipe is now closed.
                            if not data:
                                sel.unregister(event.fileobj)
                                del pipes[event.fileobj]

                                # if we're not linechopping, flush
                                # out the remaining data now.
                                if can_chop and linecount >= INF:
                                    yield vfd, self.buf
                                    del self.buf[:]
                                break

                            # if this pipe can do line chopping
                            if can_chop:
                                if len(self.buf) + len(data) > linebuf_max:
                                    raise subprocess.SubprocessError(
                                        "too much line data")

                                self.buf.extend(data)

                                # only chop if we care about lines.
                                if linecount < INF:
                                    npos = self.buf.rfind(b"\n")

                                    if npos < 0:
                                        break

                                    lines = self.buf[:npos].split(b"\n")

                                    for line in lines:
                                        lines_emitted += 1
                                        yield vfd, bytes(line)

                                    del self.buf[:(npos+1)]

                                # no line chopping
                                else:
                                    # yield the remaining buffer
                                    # plus the newly read data.
                                    yield vfd, self.buf
                                    del self.buf[:]

                            else:
                                # stderr data is directly yielded
                                yield vfd, data

                        elif action == selectors.EVENT_WRITE:
                            if len(input_feed) == 0:
                                sel.unregister(event.fileobj)
                                del pipes[event.fileobj]
                                break

                            nwrote = os.write(event.fd, input_feed)
                            del input_feed[:nwrote]

                        else:
                            raise Exception("invalid select action")

                    except BlockingIOError:
                        # we read all currently available data
                        break

        sel.close()

        if lines_emitted >= linecount:
            # all requested lines were fetched,
            # keep the process running.
            return

        elif linecount < INF:
            # we read less lines than required.
            raise subprocess.SubprocessError(
                "could not read required number of lines")

        # all pipes have reached EOF
        if not pipes:
            try:
                retval = self.proc.wait(get_timeout())
            except subprocess.TimeoutExpired:
                # make sure it's dead.
                self.proc.kill()
                # waiting is the best way to kill zombies.
                self.proc.wait(1)
                raise

            if retval != 0:
                raise subprocess.CalledProcessError(self.proc.args, retval)
