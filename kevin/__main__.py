"""
Program entry point
"""

import argparse
import asyncio
import logging
import os

from . import kevin
from .config import CFG
from .util import log_setup


def main():
    """ Main entry point """

    cmd = argparse.ArgumentParser(
        description="Kevin CI - the trashy continuous integration service")

    cmd.add_argument("-c", "--config", default="/etc/kevin/kevin.conf",
                     help="file name of the configuration to use.")
    cmd.add_argument("--volatile", action="store_true",
                     help=("disable persistent job storage, mainly for "
                           "testing purposes"))
    cmd.add_argument("-d", "--debug", action="store_true",
                     help="enable asyncio debugging")
    cmd.add_argument("-v", "--verbose", action="count", default=0,
                     help="increase program verbosity")
    cmd.add_argument("-q", "--quiet", action="count", default=0,
                     help="decrease program verbosity")

    args = cmd.parse_args()

    print("\x1b[1;32mKevin CI initializing...\x1b[m")

    # set up log level
    log_setup(args.verbose - args.quiet)

    # load all config files
    CFG.load(args.config)

    # pass commandline args
    CFG.set_cmdargs(args)

    # print proxy environment variables
    proxy_vars = [env_var for env_var in os.environ.keys()
                  if env_var.lower().endswith("_proxy")]
    if proxy_vars:
        logging.info("active proxy config:")
        for proxy_var in proxy_vars:
            logging.info(f"  {proxy_var}={os.environ[proxy_var]}")
    else:
        logging.info("no active proxy configuration.")

    logging.error("\x1b[1;32mKevin CI running...\x1b[m")

    try:
        asyncio.run(kevin.run(CFG), debug=args.debug)

    except KeyboardInterrupt:
        logging.info("exiting...")

    except Exception:
        logging.exception("\x1b[31;1mfatal internal exception\x1b[m")

    print("cya!")

if __name__ == '__main__':
    main()
