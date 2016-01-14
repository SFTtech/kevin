"""
Program entry point
"""

import argparse
import queue

from . import jobs
from .config import CFG
from .httpd import HTTPD


def main():
    """ Main loop """
    cmd = argparse.ArgumentParser(
        description="Kevin CI - the trashy continuous integration service")

    cmd.add_argument("-c", "--config", default="/etc/kevin/kevin.conf",
                     help="file name of the configuration to use.")
    cmd.add_argument("--volatile", action="store_true",
                     help=("disable persistent job storage, mainly for "
                           "testing purposes"))

    args = cmd.parse_args()

    print("\x1b[1;32mKevin CI initializing...\x1b[m")

    # load all config files
    CFG.load(args.config)

    # pass commandline args
    CFG.set_cmdargs(args)

    # the main job processing queue
    job_queue = queue.Queue(maxsize=CFG.max_jobs_queued)

    print("\x1b[1;32mKevin CI starting...\x1b[m")

    # start thread for receiving webhooks
    httpd = HTTPD(CFG.urlhandlers, job_queue)
    httpd.start()

    try:
        while True:
            print("\x1b[32mWaiting for job...\x1b[m")
            current_job = job_queue.get()

            # TODO: for job parallelism, fork off here:
            current_job.build()

            jobs.put_in_cache(current_job)

    except (KeyboardInterrupt, SystemExit):
        print("exiting...")
    except BaseException:
        print("\x1b[31;1mfatal exception in main loop\x1b[m")
        import traceback
        traceback.print_exc()
    finally:
        httpd.stop()


if __name__ == '__main__':
    main()
