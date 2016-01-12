"""
Utility routines.
"""

import fcntl
import os
import subprocess
import sys


def run_command(cmd, env=None):
    """
    Prints the command, then runs it, then throws RuntimeError on retval != 0.

    If a dict is given for env, those additional environment variables are
    passed.
    """

    print("\x1b[32;1m$\x1b[m " + cmd)

    cmdenv = os.environ.copy()
    if env is not None:
        cmdenv.update(env)

    retval = subprocess.call(cmd, env=cmdenv, shell=True)

    if retval != 0:
        print("\x1b[31;1mcommand returned %d\x1b[m" % retval)
        raise RuntimeError(
            "command failed: %s [%d]" % (cmd, retval))


def wrap_in_pty():
    """
    Wraps the subsequent flow of execution inside a PTY.
    Both stdout and stderr are redirected to the former stdout,
    and a file object that allows writing to the old stderr is returned.

    Note that os.getpid() will differ after calling this method.
    Don't call this method from multi-threaded applications.
    """
    # control connection: relay to stderr
    old_stderr = os.dup(sys.stderr.fileno())
    child_pid, tty_fd = os.forkpty()

    if child_pid != 0:
        # we're not the child.
        os.close(old_stderr)

    if child_pid < 0:
        raise OSError("could not fork")

    if child_pid > 0:
        # we're the parent, tasked with relaying the tty output to old stdout.
        stdout = os.fdopen(1, 'wb')
        while True:
            try:
                data = os.read(tty_fd, 8192)
            except OSError:
                # slave has been closed
                os.close(tty_fd)
                _, status = os.waitpid(child_pid, 0)
                exit(status % 128 + status // 256)

            # relay data
            stdout.write(data)
            stdout.flush()
    else:
        # we're the child, the code flow will continue here.
        # we don't want any subprocesses to access the control file.
        fcntl.fcntl(old_stderr, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
        return os.fdopen(old_stderr, 'wb')


class FatalBuildError(Exception):
    """
    Used to terminate the build with a nice error message.
    (as opposed to internal build errors, which yield a stack trace)
    """
    pass
