"""
Program entry point
"""

import argparse
import asyncio
import logging
import signal
import sys

from .config import CFG
from .httpd import HTTPD
from .jobqueue import Queue
from .util import log_setup


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
    cmd.add_argument("-v", "--verbose", action="count", default=0,
                     help="increase program verbosity")
    cmd.add_argument("-q", "--quiet", action="count", default=0,
                     help="decrease program verbosity")

    args = cmd.parse_args()

    print("\x1b[1;32mKevin CI initializing...\x1b[m")

    # set up log level
    log_setup(args.verbose - args.quiet)

    loop = asyncio.get_event_loop()

    # enable asyncio debugging
    loop.set_debug(args.debug)

    # load all config files
    CFG.load(args.config)

    # pass commandline args
    CFG.set_cmdargs(args)

    logging.error("\x1b[1;32mKevin CI starting...\x1b[m")

    # build job queue
    queue = Queue(max_running=CFG.max_jobs_running)

    # start thread for receiving webhooks
    httpd = HTTPD(CFG.urlhandlers, queue)

    job_task = loop.create_task(queue.process_jobs())

    try:
        loop.run_until_complete(job_task)

    except (KeyboardInterrupt, SystemExit):
        print("")
        logging.info("exiting...")

        # teardown
        if not job_task.done():
            # cancel all running jobs
            cancel_jobs_task = loop.create_task(queue.cancel())
            cancels = loop.run_until_complete(cancel_jobs_task)

            # cancel the job processing
            job_task.cancel()
            try:
                loop.run_until_complete(job_task)
            except asyncio.CancelledError:
                pass

        else:
            logging.warn("[main] job_task already done!")

    except Exception:
        logging.exception("\x1b[31;1mfatal internal exception\x1b[m")

    logging.info("cleaning up...")

    # run the loop one more time to process leftover tasks
    loop.stop()
    loop.run_forever()
    loop.close()

    print("cya!")

if __name__ == '__main__':
    main()
