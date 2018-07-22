"""
Controlling interface for machines hosted on falk
"""

import asyncio
import logging
import time

from falk import messages
from falk.vm import Container

from .process import SSHProcess, ProcTimeoutError


class FalkError(Exception):
    """
    Error that occurs when Falk does something fishy,
    for example provide nonsense, talk garbage or cook salmon.
    """

    def __init__(self, msg):
        super().__init__(msg)


class VMError(FalkError):
    """
    Raised when a request to a Container was not successful.
    """
    pass


class FalkVM(Container):
    """
    Provides the same interface as any machine container,
    but instead relays the commands to a falk server.

    Use this handle to interact with the VM, i.e. boot it, terminate it, ...

    An instance of this class is created by falk, it also provides cfg.
    """
    def __init__(self, cfg, run_id, falk):
        super().__init__(cfg)

        self.run_id = run_id
        self.falk = falk

    @classmethod
    def config(cls, machine_id, cfgdata, cfgpath):
        raise Exception("config() on the VM controller called")

    async def prepare(self, manage=False):
        msg = await self.falk.query(messages.Prepare(run_id=self.run_id,
                                                     manage=manage))
        if not isinstance(msg, messages.OK):
            raise VMError(f"Failed to prepare: {msg.msg}")

    async def launch(self):
        msg = await self.falk.query(messages.Launch(run_id=self.run_id))
        if not isinstance(msg, messages.OK):
            raise VMError(f"Failed to launch machine: {msg.msg}")

    async def status(self):
        return await self.falk.query(messages.Status(run_id=self.run_id))

    async def is_running(self):
        # we have to implement it because @abstractmethod, but
        # we override `status` as well, so it's never called.
        raise Exception("VM proxy 'is_running' should never be called!")

    async def terminate(self):
        msg = await self.falk.query(messages.Terminate(run_id=self.run_id))
        if not isinstance(msg, messages.OK):
            raise VMError(f"Failed to kill machine: {msg.msg}")

    async def cleanup(self):
        msg = await self.falk.query(messages.Cleanup(run_id=self.run_id))
        if not isinstance(msg, messages.OK):
            raise VMError(f"Failed to clean up: {msg.msg}")
        return msg

    async def wait_for_ssh_port(self, timeout=60, retry_delay=0.2,
                                try_timeout=15):
        """
        Loops until the SSH port is open.
        raises ProcTimeoutError on timeout.
        """

        # TODO: provide the loop as optional constructor argument
        loop = asyncio.get_event_loop()

        raw_acquired = False
        endtime = time.time() + timeout
        while True:
            await asyncio.sleep(retry_delay)

            if not raw_acquired:
                logging.debug("testing for ssh port...")

                established = loop.create_future()

                def connection_made(reader, writer):
                    """ called when the connection was made """
                    del reader, writer  # unused
                    established.set_result(True)

                try:
                    transp, _ = await loop.create_connection(
                        lambda: asyncio.StreamReaderProtocol(
                            asyncio.StreamReader(), connection_made
                        ), self.ssh_host, self.ssh_port)

                except ConnectionRefusedError:
                    logging.debug("    \x1b[31;5mrefused\x1b[m!")

                except Exception as exc:
                    logging.error("error creating connection: %s", exc)

                else:
                    try:
                        await asyncio.wait_for(established,
                                               timeout=try_timeout)
                        raw_acquired = True
                        logging.debug("    \x1b[32;5mopen\x1b[m!")
                        transp.close()
                        continue
                    except asyncio.TimeoutError:
                        logging.debug("    \x1b[31;5mtimeout\x1b[m!")

            else:
                logging.debug("testing for ssh service on port...")

                async with SSHProcess(["true"],
                                      self.ssh_user,
                                      self.ssh_host,
                                      self.ssh_port,
                                      self.ssh_known_host_key) as proc:

                    try:
                        ret = await proc.wait_for(try_timeout)

                        if ret == 0:
                            logging.debug("    \x1b[32;5;1msuccess\x1b[m!")
                            break
                        else:
                            logging.debug("    \x1b[31;5;1mfailed\x1b[m!")

                    except ProcTimeoutError:
                        logging.debug("    \x1b[31;5;1mtimeout\x1b[m!")

            if time.time() > endtime:
                logging.debug("\x1b[31mTIMEOUT\x1b[m")
                if raw_acquired:
                    logging.info("TCP connection established, but no SSH.")
                    if self.ssh_known_host_key is not None:
                        logging.info(" Are you sure the ssh key is correct?")
                        logging.info(" -> %s", self.ssh_known_host_key)

                raise ProcTimeoutError(["ssh", "%s@%s:%s" % (
                    self.ssh_user,
                    self.ssh_host,
                    self.ssh_port)], timeout)

    async def wait_for_shutdown(self, timeout=20):
        """
        Request from falk so he tells us when the machine is dead.
        """
        msg = await self.falk.query(messages.ShutdownWait(run_id=self.run_id,
                                                          timeout=timeout))
        if not isinstance(msg, messages.OK):
            raise VMError(f"Failed to wait for shutdown: {msg.msg}")

        return msg
