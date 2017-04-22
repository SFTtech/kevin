#!/usr/bin/env python3

"""
Simulates the server-side pull request api.

Delivers a pull request hook to the specified url,
then waits for interaction.
"""

import argparse
import asyncio

from . import github
from ..util import log_setup


def main():
    cmd = argparse.ArgumentParser()
    cmd.add_argument("repo", help="clone url/path to the test repo")
    cmd.add_argument("project", help="project to trigger the build for")
    cmd.add_argument("config_file", help="config file of to-be-tested kevin")
    cmd.add_argument("-p", "--port", type=int, default=8423,
                     help="port to run the simulation on")
    cmd.add_argument("-l", "--listen", default="127.0.0.1",
                     help="address to listen on for requests")
    cmd.add_argument("--local-repo", action="store_true",
                     help=("serve a filesystem-local repo via http. "
                           "beware: provide the .git of that repo! "
                           "`git update-server-info` is called on that!"))
    cmd.add_argument("--local-repo-address", default="10.0.2.2",
                     help=("the vm can reach this simulator "
                           "under the given address."))
    cmd.add_argument("-d", "--debug", action="store_true",
                     help="enable asyncio debugging")
    cmd.add_argument("-v", "--verbose", action="count", default=0,
                     help="increase program verbosity")
    cmd.add_argument("-q", "--quiet", action="count", default=0,
                     help="decrease program verbosity")

    sp = cmd.add_subparsers(dest="module")

    # call argparser hooks
    github.GitHub.argparser(sp)

    args = cmd.parse_args()

    # set up log level
    log_setup(args.verbose - args.quiet)

    # enable asyncio debugging
    loop = asyncio.get_event_loop()
    loop.set_debug(args.debug)

    if args.module is None:
        cmd.print_help()
    else:
        service = args.service(args)
        service.run()


if __name__ == "__main__":
    main()
