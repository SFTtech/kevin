"""
This is the base for Kevin's message bus.
A watchable can be watched by a watcher.
"""

from __future__ import annotations

import asyncio
import typing

from .watcher import Watcher
from .update import Update


class Watchable:
    """
    Abstract watchable which can be watched by a Watcher.
    """

    def __init__(self) -> None:
        self._watchers: set[Watcher] = set()
        self._watchers_lock = asyncio.Lock()

    async def register_watcher(self, watcher: Watcher):
        """
        Register a watcher object,
        which gets updates sent by send_update(update).
        """

        if not isinstance(watcher, Watcher):
            raise Exception("invalid watcher type: %s" % type(watcher))

        self._watchers.add(watcher)

        await self.on_watcher_registered(watcher)

    async def on_watcher_registered(self, watcher: Watcher):
        """
        Custom actions when a watcher subscribes for receiving new updates
        """
        pass

    def deregister_watcher(self, watcher: Watcher, missing_ok: bool = False):
        """ Un-subscribe a watcher from the notification list """
        if missing_ok:
            self._watchers.discard(watcher)
        else:
            self._watchers.remove(watcher)

    def on_watcher_deregistered(self, watcher: Watcher):
        """ Custom actions when a watcher unsubscribes """
        pass

    async def send_update(self, update: Update,
                          exclude: typing.Callable[[Watcher], bool] | None = None,
                          **kwargs):
        """
        Send an update to all registered watchers
        Exclude: callable that can exclude subscribers from
        receiving the update. (called with func(subscriber))
        """

        # for enhanced debugging experience,
        # usable in the non-existant case that kevin has bugs.
        #if isinstance(update, Update):
        #    print(f"{self} => {type(update)}= {update.dump()}")

        await self.on_send_update(update, **kwargs)

        # copy list of watchers so an update can add and remove watchers
        for watcher in self._watchers.copy():
            if exclude and exclude(watcher):
                continue

            await watcher.on_update(update)

    async def on_send_update(self, update: Update, **kwargs):
        """ Called when an update is about to be sent """
        pass
