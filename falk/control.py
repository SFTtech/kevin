"""
Controlling interface for machines hosted on falk
"""


from . import messages
from .vm import Container

class VMError(Exception):
    """
    Raised when a request to a Container was not successful.
    """
    pass


class VM(Container):
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

    def config(cls, machine_id, cfgdata, cfgpath):
        return

    def prepare(self, manage=False):
        msg = self.falk.query(messages.Prepare(run_id=self.run_id,
                                               manage=manage))
        if not isinstance(msg, messages.OK):
            raise VMError("Failed to prepare: %s" % msg.msg)

    def launch(self):
        msg = self.falk.query(messages.Launch(run_id=self.run_id))
        if not isinstance(msg, messages.OK):
            raise VMError("Failed to launch machine: %s" % msg.msg)

    def status(self):
        return self.falk.query(messages.Status(run_id=self.run_id))

    def is_running(self):
        # we have to implement it because @abstractmethod, but
        # we override `status` as well, so it's never called.
        raise Exception("VM proxy 'is_running' should never be called!")

    def terminate(self):
        msg = self.falk.query(messages.Terminate(run_id=self.run_id))
        if not isinstance(msg, messages.OK):
            raise VMError("Failed to kill machine: %s" % msg.msg)

    def cleanup(self):
        return self.falk.query(messages.Cleanup(run_id=self.run_id))
        if not isinstance(msg, messages.OK):
            raise VMError("Failed to clean up: %s" % msg.msg)
