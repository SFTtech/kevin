"""
Various utility functions.
"""

# convenience infinity.
INF = float("inf")


def coroutine(func):
    """
    Utility decorator for (receiving) coroutines.
    Consumes the first generated item.
    """
    def inner(*args, **kwargs):
        """ @coroutine wrapper """
        coro = func(*args, **kwargs)
        next(coro)
        return coro

    return inner
