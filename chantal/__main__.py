"""
CLI entry point for chantal.
"""

import sys
import traceback

from .build import build_job
from .util import wrap_in_pty, FatalBuildError
from . import msg


def main():
    """
    Takes clone url and commit sha from sys.argv,
    builds the project,
    and reports progress and builder stdout to stdout.
    Requires no stdin.
    """
    # calls forkpty(), returns as the child.
    # MSG_CHANNEL points to the parent's stderr;
    # all stdout/stderr is relayed to the parent's stdout.
    msg.MSG_CHANNEL = wrap_in_pty()

    try:
        if len(sys.argv) != 4:
            raise ValueError("usage: chantal clone_url commit_sha cfgfile_name")
        clone_url, commit_sha, desc_file = sys.argv[1:]

        build_job(clone_url, commit_sha, desc_file)
    except FatalBuildError as exc:
        msg.msg(
            cmd="build-state",
            state="error",
            text=str(exc)
        )
        print("\x1b[31;1mFATAL\x1b[m " + str(exc))
    except BaseException as exc:
        msg.msg(
            cmd="build-state",
            state="error",
            text="Internal error in Chantal: %r" % exc
        )
        print("\x1b[31;1;5minternal error\x1b[m")
        traceback.print_exc()
    else:
        print("\n\x1b[1mDone.\x1b[m")

if __name__ == '__main__':
    main()
