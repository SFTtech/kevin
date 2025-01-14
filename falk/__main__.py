"""
Falk is the VM provider for Kevin CI.
"""

import argparse
import asyncio
import logging

from kevin.util import log_setup

from . import Falk
from .config import CFG


def main():
    """ Falk service launch """

    cmd = argparse.ArgumentParser(
        description="Kevin CI Falk - VM provider")

    cmd.add_argument("-c", "--config", default="/etc/kevin/falk.conf",
                     help="file name of the configuration to use.")
    cmd.add_argument("-d", "--debug", action="store_true",
                     help="enable asyncio debugging")
    cmd.add_argument("-v", "--verbose", action="count", default=0,
                     help="increase program verbosity")
    cmd.add_argument("-q", "--quiet", action="count", default=0,
                     help="decrease program verbosity")

    args = cmd.parse_args()

    print("\x1b[1;32mFalk machine service initializing...\x1b[m")

    log_setup(args.verbose - args.quiet)

    loop = asyncio.new_event_loop()

    # enable asyncio debugging
    loop.set_debug(args.debug)

    # parse config
    logging.debug("[cfg] loading...")
    CFG.load(args.config)

    logging.error("\x1b[1;32mstarting falk...\x1b[m")

    # state storage
    falk = Falk()
    falk.prepare_socket()
    try:
        asyncio.run(falk.run(), debug=args.debug)
    except KeyboardInterrupt:
        pass

    print("cya!")


if __name__ == "__main__":
    main()
