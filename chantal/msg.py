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


def stdout(text):
    """
    Sends a stdout message.
    """
    msg(cmd="stdout", text=text)


def job_state(state, text):
    """
    Sends a job state message.
    """
    msg(cmd="job-state", state=state, text=text)


def step_state(step, state, text):
    """
    Sends a step state message, if the step was not marked hidden.
    """
    if step.hidden:
        return

    msg(cmd="step-state", step=step.name, state=state, text=text)


def output_item(name):
    """
    Sends an output-item message.
    """
    msg(cmd="output-item", name=name)


def output_file(path, size):
    """
    Sends an output file message.
    Send 'size' bytes of raw data using raw_msg immediately afterwards.
    """
    msg(cmd="output-file", path=path, size=size)


def output_dir(path):
    """
    Sends an output dir message.
    """
    msg(cmd="output-dir", path=path)
