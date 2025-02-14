from __future__ import annotations

from collections import OrderedDict

from typing import Callable, TypeVar, Generic

K = TypeVar("K")
V = TypeVar("V")


delete_check_t = Callable[[V, Callable[[], None]], bool]
revive_t = Callable[[V], None]


class LRUStore(Generic[K, V]):
    def __init__(self, max_size: int,
                 max_killmap_size: int = 10,
                 delete_check: delete_check_t[V] | None = None,
                 revive: revive_t[V] | None = None) -> None:
        """
        max_size:
            number of entries until the oldest gets deleted
        max_killmap_size:
            number of entries to keep that couldn't get deleted due to `delete_check`
            when exceeded, delete the oldest killed entry first anyway.
        delete_check: fun(value, deleter) -> bool:
            function that is called when item should be deleted.
            if it returns False, item can't be deleted yet.
            when the item is ready for deletion, it must call deleter()
        revive: fun(value) -> bool:
            function called when item is scheduled for deletion, but
            was revived while waiting.
            this is used to remove the somewhere-registered deleter set in `delete_check` again.
        """
        assert max_size >= 1

        self._max_size = max_size
        self._max_killmap_size = max_killmap_size
        self._map: OrderedDict[K, V] = OrderedDict()
        self._killmap: OrderedDict[K, V] = OrderedDict()

        self._delcheck: delete_check_t[V] | None = delete_check
        self._revive: revive_t[V] | None = revive

    def __delitem__(self, key: K):
        """
        remove an item explicitly
        """
        self._map.pop(key, None)
        self._killmap.pop(key, None)

    def __setitem__(self, key: K, val: V) -> None:
        """
        store a new entry. drop the oldest if store is full.
        """
        known = self._map.get(key)
        if known is not None:
            # move to end with new value
            self._map.move_to_end(key, last=True)
            return

        self._map[key] = val

        if len(self._map) > self._max_size:
            delkey, delval = self._map.popitem(last=False)
            print("xxx deleting")
            if self._delcheck:
                print("xxx in killmap")
                # if deletion should be postponed, hold reference in separate killmap
                def delete_item():
                    del self._killmap[delkey]
                    print("xxx killed from killmap")
                if not self._delcheck(delval, delete_item):
                    self._killmap[delkey] = delval

                if len(self._killmap) > self._max_killmap_size:
                    # delete the oldest kill item anyway
                    self._killmap.popitem(last=False)

    def get(self, key: K, default: V | None = None) -> V | None:
        item = self._map.get(key)
        # bump priority to max
        if item is not None:
            self._map.move_to_end(key, last=True)
            return item

        # revive if scheduled for deletion
        to_kill = self._killmap.get(key)
        if to_kill is not None:
            try:
                if self._revive:
                    self._revive(to_kill)
            finally:
                del self._killmap[key]
                self[key] = to_kill
            return to_kill

        return None
