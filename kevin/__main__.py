"""
Program entry point
"""

import argparse
import asyncio
import traceback
import sys

from .config import CFG
from .httpd import HTTPD
from .jobqueue import Queue, process_jobs


def main():
    """ Main entry point """

    if sys.version_info < (3, 5):
        print("Kevin CI \x1b[1;31mrequires >=python-3.5\x1b[m"
              "\n\x1b[32mYou have:\x1b[m %s" % sys.version)
        exit(1)

    cmd = argparse.ArgumentParser(
        description="Kevin CI - the trashy continuous integration service")

    cmd.add_argument("-c", "--config", default="/etc/kevin/kevin.conf",
                     help="file name of the configuration to use.")
    cmd.add_argument("--volatile", action="store_true",
                     help=("disable persistent job storage, mainly for "
                           "testing purposes"))
    cmd.add_argument("-d", "--debug", action="store_true",
                     help="enable asyncio debugging")

    args = cmd.parse_args()

    print("\x1b[1;32mKevin CI initializing...\x1b[m")

    loop = asyncio.get_event_loop()

    # enable asyncio debugging
    loop.set_debug(args.debug)

    # load all config files
    CFG.load(args.config)

    # pass commandline args
    CFG.set_cmdargs(args)

    print("\x1b[1;32mKevin CI starting...\x1b[m")

    # build job queue
    queue = Queue()

    # start thread for receiving webhooks
    httpd = HTTPD(CFG.urlhandlers, queue)

    try:
        job_crunsher = loop.create_task(process_jobs(queue))

        loop.run_until_complete(job_crunsher)

    except (KeyboardInterrupt, SystemExit):
        print("\nexiting...")

    except BaseException:
        print("\x1b[31;1mfatal internal exception\x1b[m")
        traceback.print_exc()

    # teardown
    if not job_crunsher.done():
        job_crunsher.cancel()

    loop.stop()
    loop.run_forever()
    loop.close()

    print("cya!")

if __name__ == '__main__':
    main()
