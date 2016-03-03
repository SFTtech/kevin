"""
Job watching.
You can receive job updates with a Watcher.
"""


class Watchable:
    """
    Abstract watchable which can be watched by a Watcher.
    """

    def __init__(self):
        self.watchers = set()

    def watch(self, watcher):
        """
        Register a watcher object,
        which gets updates sent by send_update(update).
        """

        if not isinstance(watcher, Watcher):
            raise Exception("invalid watcher type: %s" % type(watcher))

        self.watchers.add(watcher)
        self.on_watch(watcher)

    def on_watch(self, watcher):
        """
        Custom actions when a watcher subscribes for receiving new updates
        """
        pass

    def unwatch(self, watcher):
        """ Un-subscribe a watcher from the notification list """
        self.watchers.remove(watcher)

    def on_unwatch(self, watcher):
        """ Custom actions when a watcher unsubscribes """
        pass

    def send_update(self, update, **kwargs):
        """ Send an update to all registered watchers """
        self.on_send_update(update, **kwargs)

        for watcher in self.watchers:
            watcher.on_update(update)

    def on_send_update(self, update, **kwargs):
        """ Called when an update is about to be sent """
        pass


class Watcher:
    """
    Abstract event watcher. Gets notified by a Watchable.

    When registered to SomeWatchable.watch(Watcher(...)),
    each update will be supplied to the watcher then.
    """

    def on_update(self, update):
        """
        Process the update here.
        """
        pass
