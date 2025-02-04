"""
Find matching justins to distribute jobs on.
"""

import asyncio
import logging
import random

from collections import defaultdict

from .justin import JustinSSH, JustinSocket, JustinError
from .justin_machine import MachineError


class JobManager:
    """
    Keeps an overview about the reachable justins and their VMs.

    Used by Job.run() to provide a JustinMachine.
    """

    # TODO: better selection if this justin is suitable,
    #       e.g. has the resources to spawn the machine.
    #       may involve further queries to that justin.

    def __init__(self, loop, config):
        self.loop = loop

        # lookup which justins provide the machine
        # {machinename: {justin0: {vm_id0, ...}}
        self.machines = defaultdict(lambda: defaultdict(set))

        # list of known justin connections: {name: Justin}
        self.justins = dict()

        # queue where lost connections will be stuffed into
        self.pending_justins = asyncio.Queue()

        # set of tasks that try to reconnect to disconnected justins
        self.running_reconnects = set()

        # create justins from the config
        for justinname, justincfg in config.justins.items():
            if justincfg["connection"] == "ssh":
                host, port = justincfg["location"]
                justin = JustinSSH(justinname,
                               host, port,
                               justincfg["user"],
                               justincfg["key"],
                               loop=self.loop)

            elif justincfg["connection"] == "unix":
                justin = JustinSocket(justinname,
                                  justincfg["location"], justincfg["user"],
                                  loop=self.loop)

            # TODO: allow justin bypass by launching VM locally without a
            #       justin daemon (that is the justin.JustinVirtual).
            # elif justincfg["connection"] == "virtual":
            # justin = JustinVirtual()

            else:
                raise Exception("unknown justin connection type: %s -> %s" % (
                    justinname, justincfg["connection"]))

            # remember the justin by name
            self.justins[justinname] = justin

            # when justin disconnects, perform the reconnect.
            justin.on_disconnect(self.justin_lost)

            # none of the justins is connected initially
            self.pending_justins.put_nowait(justin)

    async def run(self):
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._process())
        except asyncio.CancelledError:
            await self._shutdown()
            raise

    async def _process(self):
        """
        create control connections to all known justins.
        """
        while True:
            # wait for any justin that got lost.
            justin = await self.pending_justins.get()

            if not justin.active:
                logging.info(f"dropping deactivaved justin {justin}")
                continue

            logging.info(f"connecting pending justin {justin}...")

            # TODO: drop it from the vm availability list
            reconnection = self.loop.create_task(self.reconnect(justin))

            # save the pending reconnects in a set
            self.running_reconnects.add(reconnection)
            reconnection.add_done_callback(
                lambda fut: self.running_reconnects.remove(reconnection)
            )

    async def reconnect(self, justin, try_interval=30):
        """ tries to reconnect a justin """

        # try reconnecting forever
        while True:
            try:
                await self.connect_to_justin(justin)
                break

            except JustinError as exc:
                # connection rejections, auth problems, ...

                logging.warning(f"failed communicating "
                                f"with justin '{justin.name}'")
                logging.warning(f"\x1b[31merror\x1b[m: $ {exc}")
                logging.warning("  are you sure that justin entry "
                                f"'{justin.name}' (= {justin}) "
                                f"is valid and running?")
                logging.warning("  I'll retry connecting in "
                                f"{try_interval} seconds...")

                # clean up justin
                await justin.close()
                await asyncio.sleep(try_interval)

            except asyncio.CancelledError:
                raise

            except Exception:
                logging.exception(f"Fatal error while reconnecting to {justin}")
                justin.active = False
                break


    async def connect_to_justin(self, justin):
        """
        Connect to a Justin VM provider.
        When this function does not except,
        the connection is assumed to be sucessful.
        """
        # TODO: refresh vm lists for reconnect

        await justin.create()

        justin_vms = await justin.get_vms()

        # vm_id, (vmclassname, vm_name)
        for vm_id, (vm_type, vm_name) in justin_vms.items():
            del vm_type  # unused
            if vm_name not in self.machines:
                logging.info("container '%s' now available!", vm_name)
            self.machines[vm_name][justin].add(vm_id)

    def justin_lost(self, justin):
        """
        Called when a justin lost its connection.

        If a connection can't be established,
        this function is called as well!
        """

        # count how many justins provide a vm
        provided_count = defaultdict(lambda: 0)
        for vm_name, justin_providers in self.machines.items():
            justin_providers.pop(justin, None)
            provided_count[vm_name] += len(justin_providers.keys())

        # remove unavailable vms
        for vm_name, count in provided_count.items():
            if count == 0:
                logging.info("machine '%s' no longer available", vm_name)
                del self.machines[vm_name]

        # and queue the justin for reconnection
        self.pending_justins.put_nowait(justin)

    async def get_machine(self, name):
        """
        return a JustinMachine that matches the given name.
        """

        candidate_justins = self.machines.get(name)
        if not candidate_justins:
            raise MachineError(f"No justin could provide {name}")

        # this could need a separate datastructure....
        # no random dict choice available otherwise.
        justin = random.choice(list(candidate_justins.keys()))

        # get a machine
        vm_id = random.sample(sorted(candidate_justins[justin]), 1)[0]

        # TODO: if this justin doesn't wanna spawn the vm, try another one.
        return await justin.create_vm(vm_id)

    async def _shutdown(self):
        """
        Terminate Justin connections.
        """

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

        # close all justin connections
        for _, justin in self.justins.items():
            await justin.close()
