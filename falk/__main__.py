"""
Falk is the VM provider for Kevin CI.
"""

import argparse
import asyncio
import logging
import os
import shutil

from kevin.util import log_setup

from . import Falk
from .config import CFG
from .protocol import FalkProto


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

    loop = asyncio.get_event_loop()

    # enable asyncio debugging
    loop.set_debug(args.debug)

    # parse config
    CFG.load(args.config)

    try:
        os.unlink(CFG.control_socket)
    except OSError:
        if os.path.exists(CFG.control_socket):
            raise
        else:
            sockdir = os.path.dirname(CFG.control_socket)
            if not os.path.exists(sockdir):
                try:
                    logging.info("creating socket directory '%s'", sockdir)
                    os.makedirs(sockdir, exist_ok=True)
                except PermissionError as exc:
                    raise exc from None

    logging.error("\x1b[1;32mstarting falk...\x1b[m")

    # state storage
    falk = Falk()

    logging.warning("listening on '%s'...", CFG.control_socket)

    proto_tasks = set()

    def create_proto():
        """ creates the asyncio protocol instance """
        proto = FalkProto(falk)

        # create message "worker" task
        proto_task = loop.create_task(proto.process_messages())
        proto_tasks.add(proto_task)

        proto_task.add_done_callback(
            lambda fut: proto_tasks.remove(proto_task))

        return proto

    srv_coro = loop.create_unix_server(create_proto, CFG.control_socket)
    server = loop.run_until_complete(srv_coro)

    if CFG.control_socket_group:
        # this only works if the current user is a member of the
        # target group!
        shutil.chown(CFG.control_socket, None, CFG.control_socket_group)

    if CFG.control_socket_permissions:
        mode = int(CFG.control_socket_permissions, 8)
        os.chmod(CFG.control_socket, mode)

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print("\nexiting...")

    logging.warning("served %d connections", falk.handle_id)

    logging.info("cleaning up...")

    for proto_task in proto_tasks:
        proto_task.cancel()

    # execute the cancellations
    loop.run_until_complete(asyncio.gather(*proto_tasks,
                                           return_exceptions=True))

    # server teardown
    server.close()
    loop.run_until_complete(server.wait_closed())

    loop.stop()
    loop.run_forever()
    loop.close()

    print("cya!")


if __name__ == "__main__":
    main()
