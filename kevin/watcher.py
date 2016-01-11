"""
Job watching.
You can receive job updates with a Watcher.
"""

from abc import ABCMeta, abstractmethod


class Watcher(metaclass=ABCMeta):
    """
    Abstract job watcher.

    When registered to job.watch(Watcher(...)),
    each job update will be supplied to the watcher then.
    """

    @abstractmethod
    def new_update(self, update):
        """
        Process the JobUpdate here.
        """
        pass
