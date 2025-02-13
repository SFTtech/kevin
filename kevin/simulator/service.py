"""
Definitions for common functionality of simulated services.
"""

from __future__ import annotations

import ipaddress
from argparse import Namespace

from ..config import Config


class Service:
    """
    Base class for a simulated service.
    """

    def __init__(self, args: Namespace) -> None:
        self.cfg = Config()
        self.cfg.load(args.config_file)

        # git repo serving:
        self.local_repo = args.local_repo
        self.local_repo_address = args.local_repo_address
        self.repo_server: str | None = None

        # repo config
        self.repo = args.repo
        self.branch = args.branch  # name or None
        self.commit = args.commit  # commit hash or None
        self.project = args.project
        if self.project not in self.cfg.projects:
            raise ValueError("unknown project '%s', available: %s" % (
                self.project, self.cfg.projects.keys()
            ))

        # simulator reachability:
        self.port = args.port
        self.listen = ipaddress.ip_address(args.listen)

    @classmethod
    def argparser(cls, subparsers):
        """ implement to add a service-specific argparser """
        raise NotImplementedError()

    async def run(self):
        """ simulator-specific code """
        raise NotImplementedError()
