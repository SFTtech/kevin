"""
Code for sending messages to Kevin.

Uses the 'w'-mode fileobj MSG_CHANNEL (must be set manually).
"""

import json


MSG_CHANNEL = None


def msg(**kwargs):
    """
    Writes a JSON-ified version of kwargs to the msg stream.
    """
    MSG_CHANNEL.write(json.dumps(kwargs).encode())
    MSG_CHANNEL.write(b'\n')
    MSG_CHANNEL.flush()


def raw_msg(data):
    """
    Writes a raw bytes object to the msg stream.

    The other side must be informed about the raw data with an appropriate
    json message, or the behavior will be obviously undefined.
    """
    MSG_CHANNEL.write(data)
    MSG_CHANNEL.flush()
