"""
Falk interaction message definitions
"""

import json
import shlex

from abc import ABCMeta, abstractmethod
from enum import Enum


# class name => class mapping
MESSAGE_TYPES = dict()


class MessageMeta(ABCMeta):
    """
    Message metaclass.
    Adds the message types to the lookup dict.
    """
    def __init__(cls, name, bases, classdict):
        super().__init__(name, bases, classdict)
        MESSAGE_TYPES[name] = cls


class Message(metaclass=MessageMeta):
    """
    JSON message base class for WebSocket API
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

    def pack(self):
        """
        Returns a JSON-serialized string of self (via self.dump()).
        This string will be broadcast via WebSocket and saved to disk.
        """
        result = self.dump()
        result['class'] = type(self).__name__
        data = json.dumps(result).encode()

        return b"".join((data, b"\n"))

    @staticmethod
    def construct(msg):
        """
        Constructs a Message object from json.
        """

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
    def __init__(self):
        pass

class ListProjects(Message):
    """
    List all projects.
    """
    def __init__(self):
        pass

class SubscribeToBuild(Message):
    """
    List all projects.
    """
    def __init__(self, project, commit_hash):
        self.project = project
        self.commit_hash = commit_hash
