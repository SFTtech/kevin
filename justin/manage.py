#!/usr/bin/env python3

"""
SSH client for a VM managed by justin.
"""

import argparse
import asyncio
import logging

from kevin.justin import JustinSSH, JustinSocket
from kevin.process import SSHProcess
from kevin.util import parse_listen_entry, log_setup


async def spawn_shell(justin, vm_id, volatile, command):
    """
    Spawns an interactive shell with justin.
    """

    logging.debug("connecting to justin...")
    await justin.create()

    logging.debug("looking up machine '%s'...", vm_id)
    vm = await justin.create_vm(vm_id)

    if vm is None:
        raise Exception("vm '%s' was not found on justin '%s'. "
                        "available:\n%s" % (
                            vm_id, justin, await justin.get_vms()))

    manage = not volatile
    logging.debug("preparing and launching machine (manage=%s)..." % manage)
    await vm.prepare(manage=manage)
    await vm.launch()

    logging.debug("VM launched, waiting for ssh...")
    await vm.wait_for_ssh_port()

    if manage:
        logging.warning("please shut down the VM gracefully "
                        "to avoid data loss (=> `sudo poweroff`)")

    # ssh into the machine, force tty allocation
    async with SSHProcess(command,
                          vm.ssh_user, vm.ssh_host,
                          vm.ssh_port, vm.ssh_known_host_key, pipes=False,
                          options=["-t"]) as proc:
        ret = await proc.wait()

    # wait for the machine to exit gracefully
    wait_time = 30
    logging.warning(f"waiting {wait_time}s for machine to shut down")
    await vm.wait_for_shutdown(30)

    await vm.terminate()
    await vm.cleanup()

    return ret


def main():
    """ Connect to a pty of some vm provided by justin """

    cmd = argparse.ArgumentParser()
    cmd.add_argument("--volatile", action="store_true",
                     help="don't start the VM in management mode")
    cmd.add_argument("justin_id",
                     help=("justin connection information: "
                           "unix://socketpath, unix://user@socket "
                           "or ssh://user@host:port"))
    cmd.add_argument("vm_id", help="machine identification")
    cmd.add_argument("command", nargs="*",
                     help="command to execute. default: shell.")
    cmd.add_argument("-d", "--debug", action="store_true",
                     help="enable asyncio debugging")
    cmd.add_argument("-v", "--verbose", action="count", default=0,
                     help="increase program verbosity")
    cmd.add_argument("-q", "--quiet", action="count", default=0,
                     help="decrease program verbosity")

    args = cmd.parse_args()

    # set up log level
    log_setup(args.verbose - args.quiet)

    loop = asyncio.get_event_loop()

    # enable asyncio debugging
    loop.set_debug(args.debug)

    user, connection, location, key = parse_listen_entry(
        "justin_id", args.justin_id, require_key=False)

    if connection == "ssh":
        host, port = location
        justin = JustinSSH("manage", host, port, user, key)

    elif connection == "unix":
        justin = JustinSocket("manage", location, user)

    else:
        raise Exception("unknown justin connection type: %s" % connection)

    ret = 1
    try:
        ret = loop.run_until_complete(
            spawn_shell(justin, args.vm_id, args.volatile, args.command))

    except KeyboardInterrupt:
        print("\njustin.manage killed by keyboard interrupt\n")

    loop.stop()
    loop.run_forever()
    loop.close()

    exit(ret)

if __name__ == '__main__':
    main()
