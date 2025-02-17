"""
GitHub integration for Kevin.

Can receive WebHooks (pull_request or push)
Can send status updates to a pull requests or a pushed branch.
"""

__all__ = (
    'GitHubHook',
    'GitHubStatus',
)

from .action import GitHubHook, GitHubStatus
