"""
Utility routines.
"""

import codecs
import os
import shlex

from .msg import stdout


class CommandError(Exception):
    """
    Raised when a command to be executed fails.
    """
    pass


def filter_t(input: list[str] | tuple[str]) -> list[str]:
    return [elem for elem in input if elem]


def run_command(cmd, env, cwd=None, shell=False):
    """
    Prints the command name, then runs it.
    Throws CommandError on retval != 0.

    Env is the environment variables that are passed.
    """
    if shell:
        assert isinstance(cmd, str)
        cmd_str = cmd
    else:
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        assert isinstance(cmd, list)
        cmd_str = shlex.join(cmd)

    stdout("\x1b[32;1m$\x1b[m %s\n" % cmd_str)

    child_pid, tty_fd = os.forkpty()
    if child_pid < 0:
        raise OSError("could not fork")

    if child_pid == 0:
        # we're the child

        # enter a custom work dir
        if cwd:
            tgt = os.path.expanduser(os.path.expandvars(cwd))
            os.chdir(tgt)

        # launch the subprocess here.
        if shell:
            os.execve("/bin/sh", ["sh", "-c", cmd], env)
        else:
            os.execvpe(cmd[0], cmd, env)
        # we only reach this point if the execve has failed
        print("\x1b[31;1mcould not execve\x1b[m")
        raise SystemExit(1)

    # we're the parent; process the child's stdout and wait for it to
    # terminate.
    output_decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    while True:
        try:
            stdout(output_decoder.decode(os.read(tty_fd, 65536)))
        except OSError:
            # slave has been closed
            os.close(tty_fd)
            _, status = os.waitpid(child_pid, 0)
            retval = status % 128 + status // 256
            break

    if retval != 0:
        stdout("\x1b[31;1mcommand returned %d\x1b[m\n" % retval)
        raise CommandError("command failed: %s [%d]" % (cmd_str, retval))


class FatalBuildError(Exception):
    """
    Used to terminate the build with a nice error message.
    (as opposed to internal build errors, which yield a stack trace)
    """
    pass
