"""
Supported service base definitions.
"""

from __future__ import annotations


from .job import JobAction
from .service import Service, github, badge, symlink


def get_service(service_name: str) -> type[Service]:
    """
    get the service class for a service name
    """

    match service_name:
        case "job":
            return JobAction
        case "status_badge":
            return badge.StatusBadge
        case "symlink_branch":
            return symlink.SymlinkBranch
        case "github_webhook":
            return github.GitHubHook
        case "github_status":
            return github.GitHubStatus
        case _:
            raise ValueError(f"unknown service {service_name!r} requested")
