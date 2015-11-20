"""
Definitions for common functionality of simulated services.
"""

import ipaddress

from ..config import Config


class Service:
    """
    Base class for a simulated service.
    """

    def __init__(self, args):
        self.cfg = Config()
        self.cfg.load(args.config_file)

        self.repo = args.repo
        self.port = args.port
        self.listen = ipaddress.ip_address(args.listen)

    def run(self):
        raise NotImplementedError()
