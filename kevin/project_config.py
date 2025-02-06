"""
Project configuration file definition
"""

from __future__ import annotations

from configparser import ConfigParser
import logging
import re
import typing


from .service_meta import get_service
from .util import parse_size, parse_time

if typing.TYPE_CHECKING:
    from pathlib import Path
    from .service_meta import Service
    from .project import Project


class Config:
    """
    Configuration options for one project.

    The config consists of [project] for project settings
    followed by a number of [$service] sections.

    These activate the specified service for the project
    with the followed config.
    """

    def __init__(self, cfg_path: Path):
        self._cfg_path = cfg_path

        # parse the project config file
        self._raw: ConfigParser | None = ConfigParser()
        self._raw.read(cfg_path)

        current_section = None

        try:
            # general project config in [project]
            current_section = "project"
            projcfg = self._raw[current_section]
            self.project_name = projcfg["name"]
            self.job_max_output = parse_size(projcfg["job_max_output"])
            self.job_timeout = parse_time(projcfg["job_timeout"])
            self.job_silence_timeout = parse_time(
                projcfg["job_silence_timeout"])
            self.job_desc_file = projcfg["job_desc_file"]
            self.git_fetch_depth = projcfg["git_fetch_depth"]

        except KeyError as exc:
            logging.exception(f"\x1b[31mConfig file '{self._cfg_path}' section [{current_section}] "
                              f"is missing entry {exc!r}\x1b[m")
            exit(1)

    def get_services(self, project: Project) -> list[Service]:
        services: list[Service] = list()

        if self._raw is None:
            raise Exception("config not parsed")
        try:
            current_section = None

            # configuration for triggers and actions
            # these define what can trigger a project job,
            # and what should be done then.
            for modulename, config in self._raw.items():
                if modulename in {"DEFAULT", "project"}:
                    continue

                # for the error message generation below
                current_section = modulename

                # To support more than one config for the same service,
                # the config can be suffixed with .00, .01, ...
                hassuffix = re.match(r"([^\.]+)\.(\d+)$", modulename)
                if hassuffix:
                    modulename = hassuffix.group(1)

                # fetch the service class by the section name
                # this is a child class of "Service".
                modulecls = get_service(modulename)

                # TODO: cross references with some "include" statement
                #       to use [modulename] of some other file stated

                # create the service with the config section
                # e.g. GitHubHook(config, project)
                # or job.0 becomes JobAction
                module = modulecls(dict(config), project)

                services.append(module)

            self._raw = None

        except KeyError as exc:
            logging.exception(f"\x1b[31mConfig file '{self._cfg_path}' section [{current_section}] "
                              f"is missing entry {exc!r}\x1b[m")
            exit(1)

        return services
