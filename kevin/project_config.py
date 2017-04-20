"""
Project configuration file definition
"""

from configparser import ConfigParser
import logging
import re

from .service_meta import SERVICES
from .service import import_all as import_all_services
from .util import parse_size, parse_time


class Config:
    """
    Configuration options for one project.

    The config consists of [project] for project settings
    followed by a number of [$service] sections.

    These activate the specified service for the project
    with the followed config.
    """

    def __init__(self, filename, project):
        # parse the project config file
        raw = ConfigParser()
        raw.read(filename)

        self.services = list()

        current_section = None

        # import the available services
        import_all_services()

        try:
            # general project config in [project]
            current_section = "project"
            projcfg = raw[current_section]
            self.project_name = projcfg["name"]
            self.job_max_output = parse_size(projcfg["job_max_output"])
            self.job_timeout = parse_time(projcfg["job_timeout"])
            self.job_silence_timeout = parse_time(
                projcfg["job_silence_timeout"])
            self.job_desc_file = projcfg["job_desc_file"]

            # configuration for triggers and actions
            # these define what can trigger a project job,
            # and what should be done then.
            for modulename, config in raw.items():
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
                modulecls = SERVICES.get(modulename)
                if not modulecls:
                    raise ValueError("%s is not a valid module, "
                                     "these are: %s" % (
                                         modulename, SERVICES.keys()))

                # TODO: cross references with some "include" statement
                #       to use [modulename] of some other file stated

                # create the service with the config section
                # e.g. GitHubHook(config, project)
                module = modulecls(config, project)

                self.services.append(module)

        except KeyError as exc:
            logging.exception("\x1b[31mConfig file '%s' section [%s] "
                              "is missing entry: %s\x1b[m",
                              filename, current_section, exc)
            exit(1)
