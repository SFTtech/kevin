"""
Code for sending messages to Kevin on stdout.
"""

from __future__ import annotations

import json
import sys
import typing

if typing.TYPE_CHECKING:
    from .controlfile import Step


def msg(**kwargs) -> None:
    """
    Writes a JSON-ified version of kwargs to the msg stream.
    """
    sys.stdout.buffer.write(json.dumps(kwargs).encode())
    sys.stdout.buffer.write(b'\n')
    sys.stdout.buffer.flush()


def raw_msg(data: bytes):
    """
    Writes a raw bytes object to the msg stream.

    The other side must be informed about the raw data with an appropriate
    json message, or the behavior will obviously be undefined.
    """
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def stdout(text: str):
    """
    Sends a stdout message.
    """
    msg(cmd="stdout", text=text)


def job_state(state: str, text: str):
    """
    Sends a job state message.
    """
    msg(cmd="job-state", state=state, text=text)


def step_state(step: Step, state: str, text: str):
    """
    Sends a step state message, if the step was not marked hidden.
    """
    if step.hidden:
        return

    msg(cmd="step-state", step=step.name, state=state, text=text)


def output_item(name: str):
    """
    Sends an output-item message.
    """
    msg(cmd="output-item", name=name)


def output_file(path: str, size: int):
    """
    Sends an output file message.
    Send 'size' bytes of raw data using raw_msg immediately afterwards.
    """
    msg(cmd="output-file", path=path, size=size)


def output_dir(path: str):
    """
    Sends an output dir message.
    """
    msg(cmd="output-dir", path=path)
