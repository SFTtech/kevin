"""
Various utility functions.
"""

import fcntl
import os
import selectors
import subprocess
import time

INF = float("inf")


def coroutine(func):
    """
    Utility decorator for (receiving) coroutines.
    Consumes the first generated item.
    """
    def inner(*args, **kwargs):
        """ @coroutine wrapper """
        coro = func(*args, **kwargs)
        next(coro)
        return coro

    return inner


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


def popen_iterate(proc, timeout=INF, individual_timeout=INF,
                  kill_timeout=None):
    """
    Runs until the process terminates or times out.
    Yields tuples of (stream, data) where stream is one of
    {stdout_fileno, stderr_fileno}, and data is the bytes object
    that was written to that stream.
    Raises ProcTimeoutError if more than timeout seconds
    pass in total, or more than individual_timeout seconds pass
    during an individual wait.
    Raises CalledProcessError(str, retval) if the process fails.
    proc is guaranteed to be dead and no zombie afterwards.
    """
    start = time.monotonic()

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

    pipes = {proc.stdout.fileno(): 1, proc.stderr.fileno(): 2}

    for pipe in pipes:
        flags = fcntl.fcntl(pipe, fcntl.F_GETFL)
        fcntl.fcntl(pipe, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        sel.register(pipe, selectors.EVENT_READ, None)

    while pipes:
        waitmsg_time = time.monotonic()

        # calculate time to wait for ready file descriptors
        was_global, time_left = get_timeout(True)

        # if time_left > 0, wait for that time.
        # if <= 0, don't wait and immediately return ready fds.
        events = sel.select(time_left)
        if not events:
            # T-T-T-T-T-TIMEOUT!!!!11
            # make sure it's dead
            proc.terminate()

            try:
                proc.wait(kill_timeout)

            # still not dead, kill it again.
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                proc.wait(kill_timeout)

            if was_global:
                overtime_start = start
            else:
                overtime_start = waitmsg_time

            # if proc could be killed, report the timeout.
            raise ProcTimeoutError(
                proc.args,
                time.monotonic() - overtime_start,
                was_global,
            )

        # process all ready filedescriptors
        for event, _ in events:
            while True:
                try:
                    data = os.read(event.fd, 16384)
                    if not data:
                        # EOF
                        sel.unregister(event.fileobj)
                        del pipes[event.fileobj]
                        break
                    yield pipes[event.fileobj], data
                except BlockingIOError:
                    # we read all currently available data
                    break

    sel.close()

    # all pipes have reached EOF
    try:
        retval = proc.wait(get_timeout())
    except subprocess.TimeoutExpired:
        # make sure it's dead.
        proc.kill()
        # waiting is the best way to kill zombies.
        proc.wait(0)
        raise

    if retval != 0:
        raise subprocess.CalledProcessError(proc.args, retval)
