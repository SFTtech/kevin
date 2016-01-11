"""
Various utility functions.
"""

import re

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


# prefix to factor ** x map
SIZESUFFIX_POWER = {
    "": 0,
    "K": 1,
    "M": 2,
    "G": 3,
    "T": 4,
    "P": 5,
    "E": 6,
}


def parse_size(text):
    """
    parse a text like '10G' as 10 gigabytes = 10 * 1000**3 bytes
    returns size in bytes.
    """

    mat = re.match(r"(\d+)\s*([KMGTPE]?)(i?)B", text)
    if not mat:
        raise ValueError(
            "invalid size '%s', expected e.g. 10B, 42MiB or 1337KiB" % text)

    number = int(mat.group(1))
    suffix = mat.group(2)
    factor = 1024 if mat.group(3) else 1000

    power = SIZESUFFIX_POWER[suffix]

    return number * (factor ** power)


def parse_time(text, allow_inf=True):
    """
    parse a text like '10min' as 10 * 60 s
    returns time in seconds.
    """

    if allow_inf and text == "inf":
        return float("+inf")

    mat = re.match(r"(\d+)\s*(min|[hsm])", text)
    if not mat:
        raise ValueError(
            "invalid duration '%s', valid: 10min, 10m, 42h or 1337s" % text)

    number = int(mat.group(1))
    suffix = mat.group(2)

    factor = {"s": 1, "min": 60, "m": 60, "h": 3600}[suffix]
    return number * factor
