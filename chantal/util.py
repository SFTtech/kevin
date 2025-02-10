"""
Utility routines.
"""

import codecs
import os
import shlex

from .msg import stdout
from .error import CommandError


def filter_t(input: list[str | None] | tuple[str | None, ...]) -> list[str]:
    return [elem for elem in input if elem]


def run_command(cmd: str | list[str],
                env: dict[str, str],
                cwd: str | None = None,
                shell: bool = False,
                hide_invoc: bool = False):
    """
    Prints the command name, then runs it.
    Throws CommandError on retval != 0.

    Env is the environment variables that are passed.
    """
    cmd_list: list
    cmd_str: str
    if isinstance(cmd, str):
        cmd_list = shlex.split(cmd)
        cmd_str = cmd
    elif isinstance(cmd, list):
        cmd_list = cmd
        cmd_str = shlex.join(cmd)
    else:
        raise ValueError(f"cmd not list or str: {cmd!r}")

    if not hide_invoc:
        stdout(f"\x1b[32;1m$\x1b[m {cmd_str}\n")

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
            os.execve("/bin/sh", ["sh", "-c", cmd_str], env)
        else:
            os.execvpe(cmd_list[0], cmd_list, env)
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
