"""
CLI entry point for chantal.
"""

import argparse
import traceback
import typing

from . import Args

from .job import run_job
from .error import FatalJobError, CommandError
from .msg import job_state, stdout


def main() -> None:
    """
    Takes clone url and commit sha from sys.argv,
    builds the project,
    and reports progress/stdout via status messages
    on its stdout stream.
    Takes no stdin and produces no stdout.
    """
    try:
        cmd = argparse.ArgumentParser(
            description=("clone a repo and process the kevinfile with"
                         "build instructions. ")
        )

        cmd.add_argument("--clone", dest="clone_source",
                         help=("Location to clone the git repo from. "
                               "If not given, don't clone."))
        cmd.add_argument("--branch",
                         help=("Branch to clone. If given, clone only "
                               "this branch and no other repo history."))
        cmd.add_argument("--checkout", dest="treeish",
                         help=("Treeish (branch, hash, ...) to check out "
                               "after clone. If not given, just clone."))
        cmd.add_argument("--desc-file", dest="filename", default="kevinfile",
                         help=("Filename of the control file ('%(default)s') "
                               "within the repo folder"))
        cmd.add_argument("--desc-format", dest="format", default="makeish",
                         help="Format of the control file ('%(default)s') ")
        cmd.add_argument("--job", default="",
                         help=("Job name to let the control file "
                               "perform conditionals"))
        cmd.add_argument("--fetch-depth", type=int, default=0,
                         help=("Depth of commits to clone the repo, "
                               "use 1 to only download the latest commit"))
        cmd.add_argument("--dir", dest="work_location", default="repo",
                         help=("Directory where the git repo will be "
                               "cloned and chantal will `cd` to. "
                               "default is ./%(default)s"))

        args: Args = typing.cast(Args, cmd.parse_args())

        run_job(args)

    except (FatalJobError, CommandError) as exc:
        job_state("error", str(exc))
        stdout("\x1b[31;1mFATAL\x1b[m %s\n" % str(exc))

    except SystemExit as exc:
        if exc.code != 0:
            job_state("error", f"chantal exited with {exc.code}")
            stdout(f"\x1b[31;1mexit with\x1b[m {exc.code}\n")

    except BaseException as exc:
        job_state("error", f"Internal error in Chantal: {exc!r}")
        stdout("\x1b[31;1;5minternal error\x1b[m\n")
        traceback.print_exc()

    else:
        stdout("\n\x1b[1mDone.\x1b[m\n")


if __name__ == '__main__':
    main()
