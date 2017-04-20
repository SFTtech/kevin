"""
In this module are implementations of supported services.
Those can be triggers, actions or both.
"""


def import_all():
    """
    Register the services by importing them (the metaclass registers this).
    """

    from ..job import Job
    from .github import GitHubHook
