"""
Falk is the VM provider for Kevin CI.
"""

import argparse
import asyncio
import os
import shutil

from . import Falk
from .config import CFG
from .protocol import FalkProto


def main():
    """ Falk service launch """

    cmd = argparse.ArgumentParser(
        description="Kevin CI Falk - VM provider")

    cmd.add_argument("-c", "--config", default="/etc/kevin/falk.conf",
                     help="file name of the configuration to use.")

    args = cmd.parse_args()

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
                    print("creating socket directory '%s'" % sockdir)
                    os.makedirs(sockdir, exist_ok=True)
                except PermissionError as exc:
                    raise exc from None

    loop = asyncio.get_event_loop()

    # state storage
    falk = Falk()

    print("listening on '%s'..." % CFG.control_socket)
    proto = lambda: FalkProto(falk)
    srv_coro = loop.create_unix_server(proto, CFG.control_socket)
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
        print("exiting...")
        pass

    server.close()
    loop.run_until_complete(server.wait_closed())
    loop.close()

    print("cya!")


if __name__ == "__main__":
    main()
