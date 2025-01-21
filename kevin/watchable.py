"""
This is the base for Kevin's message bus.
A watchable can be watched by a watcher.
"""

from __future__ import annotations

import asyncio
import typing

from .watcher import Watcher

if typing.TYPE_CHECKING:
    from .update import Update


class Watchable:
    """
    Abstract watchable which can be watched by a Watcher.
    """

    def __init__(self) -> None:
        self.watchers: set[Watcher] = set()
        self.watchers_lock = asyncio.Lock()

    async def register_watcher(self, watcher: Watcher):
        """
        Register a watcher object,
        which gets updates sent by send_update(update).
        """

        if not isinstance(watcher, Watcher):
            raise Exception("invalid watcher type: %s" % type(watcher))

        self.watchers.add(watcher)

        await self.on_watcher_registered(watcher)

    async def on_watcher_registered(self, watcher: Watcher):
        """
        Custom actions when a watcher subscribes for receiving new updates
        """
        pass

    def deregister_watcher(self, watcher: Watcher):
        """ Un-subscribe a watcher from the notification list """
        self.watchers.remove(watcher)

    def on_watcher_deregistered(self, watcher: Watcher):
        """ Custom actions when a watcher unsubscribes """
        pass

    async def send_update(self, update: Update, exclude=False, **kwargs):
        """
        Send an update to all registered watchers
        Exclude: callable that can exclude subscribers from
        receiving the update. (called with func(subscriber))
        """
        self.on_send_update(update, **kwargs)

        # copy list of watchers so an update can add and remove watchers
        for watcher in self.watchers.copy():
            if exclude and exclude(watcher):
                continue

            await watcher.on_update(update)

    def on_send_update(self, update: Update, **kwargs):
        """ Called when an update is about to be sent """
        pass
