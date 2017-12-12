"""
Find matching falks to distribute jobs on.
"""

import asyncio
import logging
import random

from collections import defaultdict

from .falk import FalkSSH, FalkSocket, FalkError
from .falkvm import VMError


class JobManager:
    """
    Keeps an overview about the reachable falks and their VMs.

    Used by Job.run() to provide a FalkVM.
    """

    # TODO: better selection if this falk is suitable,
    #       e.g. has the resources to spawn the machine.
    #       may involve further queries to that falk.

    def __init__(self, loop, config):
        self.loop = loop

        # lookup which falks provide the machine
        # {machinename: {falk0: {vm_id0, ...}}
        self.machines = defaultdict(lambda: defaultdict(set))

        # list of known falk connections: {name: Falk}
        self.falks = dict()

        # queue where lost connections will be stuffed into
        self.pending_falks = asyncio.Queue()

        # set of tasks that try to reconnect to disconnected falks
        self.running_reconnects = set()

        # create falks from the config
        for falkname, falkcfg in config.falks.items():
            if falkcfg["connection"] == "ssh":
                host, port = falkcfg["location"]
                falk = FalkSSH(falkname,
                               host, port,
                               falkcfg["user"],
                               falkcfg["key"],
                               loop=self.loop)

            elif falkcfg["connection"] == "unix":
                falk = FalkSocket(falkname,
                                  falkcfg["location"], falkcfg["user"],
                                  loop=self.loop)

            # TODO: allow falk bypass by launching VM locally without a
            #       falk daemon (that is the falk.FalkVirtual).
            # elif falkcfg["connection"] == "virtual":
            # falk = FalkVirtual()

            else:
                raise Exception("unknown falk connection type: %s -> %s" % (
                    falkname, falkcfg["connection"]))

            # remember the falk by name
            self.falks[falkname] = falk

            # when falk disconnects, perform the reconnect.
            falk.on_disconnect(self.falk_lost)

            # none of the falks is connected initially
            self.pending_falks.put_nowait(falk)

        # keeping connections to job runners alive
        self.running = loop.create_task(self.run())

    async def run(self):
        """
        create control connections to all known falks.
        """

        while True:
            # wait for any falk that got lost.
            falk = await self.pending_falks.get()

            if not falk.active:
                logging.info(f"dropping deactivaved falk {falk}")
                continue

            logging.info(f"connecting pending falk {falk}...")

            # TODO: drop it from the vm availability list
            reconnection = self.loop.create_task(self.reconnect(falk))

            # save the pending reconnects in a set
            self.running_reconnects.add(reconnection)
            reconnection.add_done_callback(
                lambda fut: self.running_reconnects.remove(reconnection)
            )

    async def reconnect(self, falk, try_interval=30):
        """ tries to reconnect a falk """

        # try reconnecting forever
        while True:
            try:
                await self.connect_to_falk(falk)
                break

            except FalkError as exc:
                # connection rejections, auth problems, ...

                logging.warning(f"failed communicating "
                                f"with falk '{falk.name}'")
                logging.warning(f"\x1b[31merror\x1b[m: $ {exc}")
                logging.warning("  are you sure that falk entry "
                                f"'{falk.name}' (= {falk}) "
                                f"is valid?")

                # clean up falk
                await falk.close()
                await asyncio.sleep(try_interval)

            except asyncio.CancelledError:
                raise

            except Exception:
                logging.exception(f"Fatal error while reconnecting to {falk}")
                falk.active = False
                break


    async def connect_to_falk(self, falk):
        """
        Connect to a Falk VM provider.
        When this function does not except,
        the connection is assumed to be sucessful.
        """
        # TODO: refresh vm lists for reconnect

        await falk.create()

        falk_vms = await falk.get_vms()

        # vm_id, (vmclassname, vm_name)
        for vm_id, (vm_type, vm_name) in falk_vms.items():
            del vm_type  # unused
            if vm_name not in self.machines:
                logging.info("machine '%s' now available!", vm_name)
            self.machines[vm_name][falk].add(vm_id)

    def falk_lost(self, falk):
        """
        Called when a falk lost its connection.

        If a connection can't be established,
        this function is called as well!
        """

        # count how many falks provide a vm
        provided_count = defaultdict(lambda: 0)
        for vm_name, falk_providers in self.machines.items():
            falk_providers.pop(falk, None)
            provided_count[vm_name] += len(falk_providers.keys())

        # remove unavailable vms
        for vm_name, count in provided_count.items():
            if count == 0:
                logging.info("machine '%s' no longer available", vm_name)
                del self.machines[vm_name]

        # and queue the falk for reconnection
        self.pending_falks.put_nowait(falk)

    async def get_machine(self, name):
        """
        return a FalkVM that matches the given name.
        """

        candidate_falks = self.machines.get(name)
        if not candidate_falks:
            raise VMError(f"No falk could provide {name}")

        # this could need a separate datastructure....
        # no random dict choice available otherwise.
        falk = random.choice(list(candidate_falks.keys()))

        # get a machine
        vm_id = random.sample(candidate_falks[falk], 1)[0]

        # TODO: if this falk doesn't wanna spawn the vm, try another one.
        return await falk.create_vm(vm_id)

    async def shutdown(self):
        """
        Terminate Falk connections.
        """

        # cancel the queue processing
        if not self.running.done():
            self.running.cancel()

            try:
                await self.running
            except asyncio.CancelledError:
                pass

        expected_cancels = len(self.running_reconnects)

        # cancel the reconnects tasks
        for reconnect in self.running_reconnects:
            reconnect.cancel()


        # let all tasks run and gather their cancellation exceptions
        reconnects_canceled = [
            res for res in await asyncio.gather(
                *self.running_reconnects,
                return_exceptions=True
            )
            if isinstance(res, asyncio.CancelledError)
        ]

        if expected_cancels != len(reconnects_canceled):
            logging.warning("not all reconnects were canceled!")

        # close all falk connections
        for _, falk in self.falks.items():
            await falk.close()
