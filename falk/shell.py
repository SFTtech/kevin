#!/usr/bin/env python3

"""
SSH -> unix socket bridge for falk.

sends the ssh-forced user as login message to falk.
"""

import argparse
import fcntl
import os
import selectors
import socket
import sys

from enum import Enum

from .protocol import FalkProto
from .config import CFG
from .messages import Login



def main():
    """
    Spawns a shell that relays the messages to the falk unix socket.
    """

    cmd = argparse.ArgumentParser()
    cmd.add_argument("user", help="the user that connected to falk")
    cmd.add_argument("-c", "--config", default="/etc/kevin/falk.conf",
                     help="corresponding falk daemon config file")

    args = cmd.parse_args()

    CFG.load(args.config, shell=True)

    user = args.user
    peer = (os.environ.get("SSH_CLIENT") or "local").split()[0]

    # connection relay
    sel = selectors.DefaultSelector()

    # connect to falk unix socket.
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    try:
        sock.connect(CFG.control_socket)
    except FileNotFoundError:
        print("falk socket not found: '%s' missing" % CFG.control_socket)
        return

    class Buf(Enum):
        outbuf = 0
        inbuf = 1

    # relay buffers.
    # outbuf: unix -> stdout
    # inbuf: stdin -> unix
    outbuf = bytearray()
    inbuf = bytearray()

    # enum lookup to bytearray, as it is not hashable for the dict.
    ebuf = {
        Buf.inbuf: inbuf,
        Buf.outbuf: outbuf,
    }

    # store a maximum of 8 MiB per buffer
    # TODO: make configurable
    max_size = 8 * 1024 * 1024

    # define which fds and events are used for a buffer
    # buffer -> [(fd, event), ...]
    pipes = {
        Buf.outbuf: [(sys.stdout.fileno(), selectors.EVENT_WRITE),
                     (sock.fileno(), selectors.EVENT_READ)],
        Buf.inbuf: [(sys.stdin.fileno(), selectors.EVENT_READ),
                    (sock.fileno(), selectors.EVENT_WRITE)],
    }

    # maps {buf -> pipe}: which pipe to use to send buf to.
    write_pipes = dict()

    # {pipe -> buf}: which buffer is filled by pipe
    read_pipes = dict()

    # set streams to nonblocking and gather buffer-pipe assignments
    for buf, fdactions in pipes.items():
        for pipe, event in fdactions:
            flags = fcntl.fcntl(pipe, fcntl.F_GETFL)
            fcntl.fcntl(pipe, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            if event == selectors.EVENT_WRITE:
                write_pipes[buf] = pipe
            elif event == selectors.EVENT_READ:
                read_pipes[pipe] = buf

    def add_writer(pipe, buf, bufname):
        """
        register a pipe to wait for a can-write-to event.
        passes data to the register event
        """
        try:
            sel.register(pipe, selectors.EVENT_WRITE, buf)
        except KeyError:
            events = sel.get_key(pipe).events
            sel.modify(write_pipes[bufname],
                       events | selectors.EVENT_WRITE,
                       buf)

    def del_writer(pipe):
        """ Remove a pipe from write-to queue """
        events = sel.get_key(pipe).events

        # only event: write -> remove.
        if events == selectors.EVENT_WRITE:
            sel.unregister(pipe)
        else:
            # just remove the wait-for-write.
            sel.modify(pipe,
                       events & ~selectors.EVENT_WRITE)

    # register all wait-for-read events
    for pipe in read_pipes.keys():
        sel.register(pipe, selectors.EVENT_READ)

    # send user login message to falk
    msg = Login(user, peer)
    data = msg.pack(FalkProto.DEFAULT_MODE)
    inbuf += data
    add_writer(write_pipes[Buf.inbuf], inbuf, Buf.inbuf)

    # process events
    # TODO: use asyncio, but it's rather complicated for this use case.
    while sel.get_map().keys():
        events = sel.select()
        for event, mask in events:
            while True:
                try:
                    # perform read or write action depending on flag.
                    if mask & selectors.EVENT_READ:
                        bufname = read_pipes[event.fileobj]
                        pipe = write_pipes[bufname]
                        buf = ebuf[bufname]
                        free = max_size - len(buf)

                        data = os.read(event.fd, min(16384, free))
                        if not data:  # end of file, cya!
                            sel.unregister(event.fileobj)

                            # one of the streams closed, let's exit
                            exit()
                            break

                        buf.extend(data)

                        # the buffer now has data, enqueue send:
                        add_writer(pipe, buf, bufname)

                    if mask & selectors.EVENT_WRITE:
                        buf = event.data
                        pipe = event.fileobj
                        if not buf:
                            # no more data to send, dequeue send.
                            del_writer(pipe)
                            break

                        nwrote = os.write(event.fd, buf)
                        del buf[:nwrote]

                except BlockingIOError:
                    # we processed all currently available/writable data
                    break


if __name__ == '__main__':
    main()
else:
    raise RuntimeError("this script must be run stand-alone")
