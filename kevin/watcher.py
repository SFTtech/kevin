"""
Job watching.
You can receive job updates with a Watcher.
"""

from __future__ import annotations

import typing

if typing.TYPE_CHECKING:
    from .update import UpdateStep


class Watcher:
    """
    Abstract event watcher. Gets notified by a Watchable.

    When registered to SomeWatchable.register_watcher(Watcher(...)),
    each update will be supplied to the watcher then.
    """

    async def on_update(self, update: UpdateStep) -> None:
        """
        Process the update here.
        """
        pass
