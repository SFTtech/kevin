"""
Code for sending messages to Kevin on stdout.
"""

import json
import sys


def msg(**kwargs):
    """
    Writes a JSON-ified version of kwargs to the msg stream.
    """
    sys.stdout.buffer.write(json.dumps(kwargs).encode())
    sys.stdout.buffer.write(b'\n')
    sys.stdout.buffer.flush()


def raw_msg(data):
    """
    Writes a raw bytes object to the msg stream.

    The other side must be informed about the raw data with an appropriate
    json message, or the behavior will obviously be undefined.
    """
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
