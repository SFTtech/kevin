"""
This is the base for Kevin's message bus.
A watchable can be watched by a watcher.
"""

from .watcher import Watcher


class Watchable:
    """
    Abstract watchable which can be watched by a Watcher.
    """

    def __init__(self):
        self.watchers = set()

    async def register_watcher(self, watcher):
        """
        Register a watcher object,
        which gets updates sent by send_update(update).
        """

        if not isinstance(watcher, Watcher):
            raise Exception("invalid watcher type: %s" % type(watcher))

        self.watchers.add(watcher)
        await self.on_watcher_registered(watcher)

    async def on_watcher_registered(self, watcher):
        """
        Custom actions when a watcher subscribes for receiving new updates
        """
        pass

    def deregister_watcher(self, watcher):
        """ Un-subscribe a watcher from the notification list """
        self.watchers.remove(watcher)

    def on_watcher_deregistered(self, watcher):
        """ Custom actions when a watcher unsubscribes """
        pass

    async def send_update(self, update, exclude=False, **kwargs):
        """
        Send an update to all registered watchers
        Exclude: callable that can exclude subscribers from
        receiving the update. (called with func(subscriber))
        """
        self.on_send_update(update, **kwargs)

        for watcher in self.watchers:
            if exclude and exclude(watcher):
                continue

            await watcher.on_update(update)

    def on_send_update(self, update, **kwargs):
        """ Called when an update is about to be sent """
        pass
