"""
Program entry point
"""

import argparse

from .config import CFG
from .httpd import HTTPD
from . import jobs


def main():
    """ Main loop """
    cmd = argparse.ArgumentParser(
        description="Kevin CI - the trashy continuous integration service")

    cmd.add_argument("-c", "--config", default="/etc/kevin.conf",
                     help="file name of the configuration to use.")

    args = cmd.parse_args()

    CFG.load(args.config)

    print("\x1b[1;32mKevin CI starting...\x1b[m")

    httpd = HTTPD()
    httpd.start()

    try:
        while True:
            print("\x1b[32mWaiting for job...\x1b[m")
            current_job = httpd.get_job()
            current_job.build()
            jobs.put_in_cache(current_job)
    except (KeyboardInterrupt, SystemExit):
        print("exiting...")
    except BaseException:
        print("fatal exception in main loop")
        import traceback
        traceback.print_exc()
    finally:
        httpd.stop()


if __name__ == '__main__':
    main()
