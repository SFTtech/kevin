"""
Falk interaction message definitions
"""

import json
import shlex

from abc import ABCMeta
from enum import Enum



# class name => class mapping
MESSAGE_TYPES = dict()

# message command (e.g. "help", "login", ..) to class mapping
MESSAGE_CMD = dict()


class ProtoType(Enum):
    """
    Communicatioon protocol type.
    """
    json = "json"
    text = "text"


class ProtoNotImplementedError(NotImplementedError):
    """
    Exception thrown when some protocol handling was not yet implemented.
    """
    pass


class MessageMeta(ABCMeta):
    """
    Message metaclass.
    Adds the message types to the lookup dict.
    """
    def __init__(cls, name, bases, classdict):
        super().__init__(name, bases, classdict)
        MESSAGE_TYPES[name] = cls
        MESSAGE_CMD[cls.cmd()] = cls


class Message(metaclass=MessageMeta):
    """
    Base class for all falk communication messages.

    It support json and plaintext transmission.
    For plaintext, the `shelldump` method sends, `parse_args` receives.

    json uses `dump` method to send, and the keyword args in the constructor
    to receive.

    The `pack` method selects the correct binary conversion according to the
    requested mode -> When sending a message, do send(message.pack(mode))
    """

    # member export blacklist to prevent members from being dump()ed
    BLACKLIST = set()

    def __init__(self):
        pass

    def dump(self):
        """
        Dump members as a dict, except those in BLACKLIST.
        The dict shall be suitable for feeding back into __init__ as kwargs.
        """
        return {
            k: v for k, v in self.__dict__.items()
            if k not in self.BLACKLIST
        }

    def pack(self, mode=ProtoType.json):
        """
        Pack this message to a binary buffer.
        """

        if mode == ProtoType.json:
            data = self.json()
        elif mode == ProtoType.text:
            data = self.shelldump().encode()
        else:
            raise Exception("invalid mode.")

        return b"".join((data, b"\n"))

    @classmethod
    def cmd(cls):
        """
        return the command id for a message class
        """
        return cls.__name__.lower()

    def json(self):
        """
        Returns a JSON-serialized string of self (via self.dump()).
        This string will be broadcast via WebSocket and saved to disk.
        """
        result = self.dump()
        result['class'] = type(self).__name__
        ret = json.dumps(result).encode()
        return ret

    def shelldump(self):
        """
        Plain text representation of the received message
        """

        ret = [self.cmd()]

        for key, val in self.dump().items():
            val = shlex.quote(str(val))
            ret.append("%s=%s" % (key, val))

        return " ".join(ret)

    @staticmethod
    def construct(msg, mode=ProtoType.json):
        """
        Constructs a Message object from raw input data.

        If selected, can be a JSON-serialized string.
        The 'class' member is used to determine the subclass that shall be
        built, the rest is passed on to the constructor as kwargs.

        Else, it can be plain text (interactive shell input).
        Each message then has to provide a suitable parser function.
        """

        msg = msg.decode("utf8")

        if mode == ProtoType.json:
            try:
                data = json.loads(msg)
            except ValueError:
                raise ValueError("failed json parsing of '%s'" % msg)

            classname = data['class']
            cls = MESSAGE_TYPES.get(classname)
            if cls:
                del data['class']
                return cls(**data)
            else:
                raise ValueError("unknown message type '%s'" % classname)

        elif mode == ProtoType.text:
            argv = []
            argvk = {}

            params = shlex.split(msg)
            if not params:
                raise ValueError("no command given.")

            # first word determines the message class
            cls = MESSAGE_CMD.get(params[0])

            if not cls:
                raise ValueError("unknown command '%s'" % params[0])

            at_kwargs = False

            # grab keyword and non-keyword arguments
            for arg in params[1:]:
                split = arg.split("=", maxsplit=1)
                if len(split) == 2:
                    at_kwargs = True
                    argvk[split[0]] = split[1]

                else:
                    if at_kwargs:
                        raise ValueError(
                            "non-keyword arg (%s) after keyword arguments "
                            "have started: '%s'" % (split, params))

                    argv.append(split[0])

            # construct the message with its own parser
            return cls.parse_args(argv, argvk)
        else:
            raise Exception("invalid mode: %s" % mode)

    @classmethod
    def parse_args(cls, argv, argvk):
        """
        Parse plain-text arguments for the given message class
        Returns the constructed object for that class.
        """
        del argv, argvk
        raise ProtoNotImplementedError(
            "'%s' text parsing not implemented" % cls.__name__)

    def __repr__(self):
        """
        representation of a message
        """
        items = self.dump().items()
        clsname = type(self).__name__

        return "%s(%s)" % (clsname, ", ".join(
            ["%s=%s" % (key, val) for key, val in items]))


class Request(Message):
    """
    Plain request message, to be inherited from.
    """

    @classmethod
    def parse_args(cls, argv, argvk):
        return cls()


class RequestID(Message):
    """
    Request with one optional arg, to be inherited from.
    """
    def __init__(self, run_id=None):
        super().__init__()
        self.run_id = int(run_id) if run_id is not None else None

    @classmethod
    def parse_args(cls, argv, argvk):
        try:
            run_id = argvk.get("run_id") or\
                     (len(argv) == 1 and argv[0]) or\
                     None
            if run_id is not None:
                run_id = int(run_id)
        except IndexError:
            raise ValueError("usage: %s <run_id>" % cls.cmd()) from None

        return cls(run_id)


class Version(Message):
    """
    Protocol version information message.
    """

    def __init__(self, version):
        super().__init__()
        self.version = int(version)


class Mode(Message):
    """
    Protocol transmission mode information message.
    """

    def __init__(self, mode):
        super().__init__()
        self.check_mode(mode)
        self.mode = mode

    @staticmethod
    def check_mode(mode):
        """
        Verify if the requested protocol mode is known.
        """
        if mode not in ("json", "text"):
            raise ValueError("invalid mode %s" % mode)
        return True

    @classmethod
    def parse_args(cls, argv, argvk):
        try:
            mode = argvk.get("mode") or argv[0]
            cls.check_mode(mode)
        except (ValueError, IndexError):
            raise ValueError("usage: mode <json, text>") from None

        return cls(mode)


class Error(Message):
    """
    Failure notification
    """

    def __init__(self, msg=""):
        super().__init__()
        self.msg = msg

    @classmethod
    def parse_args(cls, argv, argvk):
        try:
            msg = argvk.get("msg") or\
                  (len(argv) == 1 and argv[0]) or\
                  ""
        except (ValueError, IndexError):
            raise ValueError("usage: error [msg]") from None

        return cls(msg)


class OK(Message):
    """
    Success notification
    """

    def __init__(self, msg=""):
        super().__init__()
        self.msg = msg

    @classmethod
    def parse_args(cls, argv, argvk):
        try:
            msg = argvk.get("msg") or\
                  (len(argv) == 1 and argv[0]) or\
                  ""
        except (ValueError, IndexError):
            raise ValueError("usage: ok [msg]") from None

        return cls(msg)


class Login(Message):
    """
    User identification message
    """
    def __init__(self, name, source):
        super().__init__()
        self.name = name
        self.source = source

    @classmethod
    def parse_args(cls, argv, argvk):
        try:
            name = argvk.get("name") or argv[0]
            source = argvk.get("source") or\
                     (len(argv) >= 2 and argv[1]) or\
                     "unknown"
        except IndexError:
            raise ValueError("usage: login <username> [source]") from None

        return cls(name=name, source=source)


class Welcome(Message):
    """
    Welcome message for a peer.
    """
    def __init__(self, msg, name):
        super().__init__()
        self.msg = msg
        self.name = name

    @classmethod
    def parse_args(cls, argv, argvk):
        try:
            msg = argvk.get("msg") or argv[0]
            name = argvk.get("name") or argv[1]
        except IndexError:
            raise ValueError("usage: welcome 'message' falkname") from None

        return cls(msg=msg, name=name)


class Help(Request):
    """
    Request help.
    """
    pass


class HelpText(Message):
    """
    Help response.
    """
    def __init__(self, text=""):
        del text
        super().__init__()

        self.text = "\n".join([
            "",
            "falk shell",
            "----------",
            "",
            " available commands:",
            "  help                   - request this text.",
            "  exit                   - quit the control connection.",
            "  mode <text|json>       - set protocol mode.",
            "  login <name>           - login as some user, + now unlocked.",
            "+ list                   - list available vms and states",
            "+ select <vmname>        - use a machine, get `runid` handle",
            "+ status [runid]         - display info about selected machine",
            "+ prepare [runid] [manage=0|1]  - prepare the machine launch",
            "+ launch [runid]          - do the machine launch",
            "+ terminate [runid]       - terminate the running machine",
            "+ cleanup [runid]         - cleanup after the vm run",
        ])

    def dump(self):
        return dict(
            text=self.text,
        )


class List(Request):
    """
    Request the list of available machines.
    """
    pass


class MachineList(Message):
    """
    Lists available machines
    machines = [(vm_id, (typename, name)), ...]
    """
    def __init__(self, machines):
        super().__init__()
        self.machines = dict(machines)


class Select(Message):
    """
    Select the VM with given name. This creates an internal handle
    and you'll get an RunID answer.
    """
    def __init__(self, name):
        super().__init__()
        if not isinstance(name, str):
            raise ValueError(f"invalid vm name: {name}")
        self.name = name

    @classmethod
    def parse_args(cls, argv, argvk):
        try:
            name = argvk.get("name") or argv[0]
        except IndexError:
            raise ValueError("usage: %s <machine_name>" % cls.cmd()) from None

        return cls(name)


class RunID(Message):
    """
    Machine selection response, contains the running machine handle number.
    """

    def __init__(self, run_id):
        super().__init__()
        self.run_id = int(run_id)


class Prepare(Message):
    """
    Prepare the launch of the machine,
    this message can decide to run the machine in management mode,
    i.e. not on a temporary disk image.
    """
    def __init__(self, run_id=None, manage=False):
        super().__init__()
        self.run_id = int(run_id) if run_id is not None else None
        self.manage = manage

    @classmethod
    def parse_args(cls, argv, argvk):
        try:
            run_id = argvk.get("run_id") or\
                     (len(argv) >= 1 and argv[0]) or\
                     None

            if run_id is not None:
                run_id = int(run_id)

            manage = argvk.get("manage") or\
                     (len(argv) >= 2 and argv[1]) or\
                     "False"

            if manage not in ("True", "False", "0", "1"):
                raise ValueError("invalid value to control management")

            manage = True if manage in ("True", "1") else False

        except ValueError:
            raise ValueError(
                "usage: prepare [runid] [manage=True|False]") from None
        except IndexError:
            manage = False

        return cls(run_id=run_id, manage=manage)


class Launch(RequestID):
    """
    Launch the VM.
    """
    pass


class Status(RequestID):
    """
    Request information about the selected VM.
    """
    pass


class VMStatus(Message):
    """
    Shows status information about a selected VM.
    """
    def __init__(self, run_id, running, ssh_user, ssh_host, ssh_port, ssh_known_host_key):
        super().__init__()
        self.run_id = int(run_id)
        self.running = running
        self.ssh_user = ssh_user
        self.ssh_host = ssh_host
        self.ssh_port = int(ssh_port)
        self.ssh_known_host_key = ssh_known_host_key


class ShutdownWait(RequestID):
    """
    Wait until the VM exits on its own.
    """
    def __init__(self, run_id, timeout):
        super().__init__(run_id)
        self.timeout = timeout


class Terminate(RequestID):
    """
    Kill the VM.
    """
    pass


class Cleanup(RequestID):
    """
    Remove leftover files from the VM run, e.g. the the temporary disk image.
    """
    pass


class Exit(Request):
    """
    Terminate the connection.
    """
    pass
