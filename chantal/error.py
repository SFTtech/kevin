class FatalJobError(Exception):
    """
    Used to terminate the build with a nice error message.
    (as opposed to internal build errors, which yield a stack trace)
    """
    pass


class CommandError(Exception):
    """
    Raised when a command to be executed fails.
    """
    pass


class OutputError(Exception):
    """
    Raised when a file output fails.
    """
    pass
