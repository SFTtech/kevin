"""
Job watching.
You can receive job updates with a Watcher.
"""

class Watcher:
    """
    Abstract event watcher. Gets notified by a Watchable.

    When registered to SomeWatchable.register_watcher(Watcher(...)),
    each update will be supplied to the watcher then.
    """

    async def on_update(self, update):
        """
        Process the update here.
        """
        pass
