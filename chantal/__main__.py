"""
CLI entry point for chantal.
"""

import argparse
import traceback

from .build import build_job
from .util import FatalBuildError
from .msg import job_state, stdout


def main():
    """
    Takes clone url and commit sha from sys.argv,
    builds the project,
    and reports progress/stdout via status messages
    on its stdout stream.
    Takes no stdin and produces no stdout.
    """
    try:
        cmd = argparse.ArgumentParser()
        cmd.add_argument("clone_url")
        cmd.add_argument("commit_sha")
        cmd.add_argument("desc_file")
        cmd.add_argument("job")
        cmd.add_argument("--shallow", type=int, default=0)
        cmd.add_argument("--folder", default="repo")

        args = cmd.parse_args()

        build_job(args)

    except FatalBuildError as exc:
        job_state("error", str(exc))
        stdout("\x1b[31;1mFATAL\x1b[m %s\n" % str(exc))
    except BaseException as exc:
        job_state("error", "Internal error in Chantal: %r" % exc)
        stdout("\x1b[31;1;5minternal error\x1b[m\n")
        traceback.print_exc()
    else:
        stdout("\n\x1b[1mDone.\x1b[m\n")

if __name__ == '__main__':
    main()
