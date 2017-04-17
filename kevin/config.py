"""
Code for loading and parsing config options.
"""

from collections import defaultdict
from configparser import ConfigParser
from pathlib import Path
import ipaddress
import logging
import os

from .project import Project
from .util import parse_connection_entry


class Config:
    """ Global configuration for kevin. """
    def __init__(self):
        self.ci_name = None
        self.max_jobs_queued = None
        self.max_jobs_running = None

        self.static_url = None
        self.mandy_url = None
        self.dyn_port = None
        self.dyn_address = ipaddress.ip_address("0.0.0.0")
        self.dyn_host = None

        self.project_folder = None
        self.output_folder = None

        self.projects = dict()
        self.falks = dict()

        self.args = None

        # maps {HookHandler class -> {kwargname -> argvalue}}
        # this basically determines the constructor arguments
        # for instanciated url handlers.
        # TODO: relocate as it's httpd.py-specific.
        #       but moving requires a lot of code overhead.
        self.urlhandlers = defaultdict(lambda: defaultdict(list))

    def set_cmdargs(self, args):
        """ Set runtime arguments """
        self.args = args

        if self.args.volatile:
            logging.warning("\x1b[1;31mYou are running in volatile mode, "
                            "nothing will be stored on disk!\x1b[m")

    def load(self, filename):
        """ Loads the attributes from the config file """
        raw = ConfigParser()

        if not Path(filename).exists():
            logging.error("\x1b[31mConfig file '%s' does not exist.\x1b[m",
                          filename)
            exit(1)

        raw.read(filename)

        # remember the current section for the error message below :)
        current_section = None

        try:
            cfglocation = Path(filename).parent

            # main config
            current_section = "kevin"
            kevin = raw[current_section]
            self.ci_name = kevin["name"]
            self.max_jobs_queued = int(kevin["max_jobs_queued"])
            self.max_jobs_running = int(kevin["max_jobs_running"])

            # project configurations.
            current_section = "projects"
            projects = raw[current_section]

            # for each project, there's a projname.conf in that folder
            projfolder = Path(projects["config_folder"])
            if not projfolder.is_absolute():
                projfolder = cfglocation / projfolder

            if not projfolder.is_dir():
                raise NotADirectoryError(str(projfolder))

            self.project_folder = projfolder

            self.output_folder = Path(projects["output_folder"])
            if not self.output_folder.is_absolute():
                self.output_folder = cfglocation / self.output_folder

            # TODO: maybe explicitly require file paths to be listed
            #       instead of iterating through all present files.
            for projectfile in self.project_folder.iterdir():
                if not str(projectfile).endswith(".conf"):
                    logging.warning("[projects] ignoring non .conf file '%s'",
                                    projectfile)
                    continue

                # create the project
                newproj = Project(str(projectfile))
                if newproj.name in self.projects:
                    raise NameError("Project '%s' defined twice!" % (
                        newproj.name))

                logging.info("[projects] loaded %s", newproj.name)

                self.projects[newproj.name] = newproj

            # merge things required by projects
            self.project_postprocess()

            # web configuration
            current_section = "web"
            web = raw[current_section]
            self.static_url = web["static_url"]
            self.mandy_url = web["mandy_url"]
            self.dyn_port = int(web["dyn_port"])
            self.dyn_host = web["dyn_host"]

            # vm providers
            current_section = "falk"
            falk_entries = raw[current_section]
            for name, url in falk_entries.items():
                if name in self.falks:
                    raise ValueError("Falk double-defined: %s" % name)

                result = parse_connection_entry(name, url, cfglocation)

                self.falks[name] = {
                    "user": result[0],
                    "connection": result[1],
                    "location": result[2],
                    "key": result[3],
                }

        except KeyError as exc:
            logging.error("\x1b[31mMissing config entry "
                          f"in section {current_section}: {exc}\x1b[m")
            exit(1)

        self.verify()

    def verify(self):
        """
        Verifies the validity of the loaded attributes
        """
        if not self.static_url.endswith('/'):
            raise ValueError("static_url must end in '/': '%s'" %
                             self.static_url)
        if not self.output_folder.is_dir():
            raise NotADirectoryError(str(self.output_folder))
        if not os.access(str(self.output_folder), os.W_OK):
            raise OSError("output_folder is not writable")

    def project_postprocess(self):
        """
        Postprocessing for all the project triggers/actions.

        Accross projects, configurations may need merging.
        Namely, if there's only one webhook handler for multiple projects,
        the configs need to be prepared for that.
        """
        # gather triggers to be installed.
        for _, project in self.projects.items():
            # for each handler type (e.g. github webhook),
            # collect all the configs
            for trigger in project.triggers:

                # install requested implicit watchers
                project.add_watchers(trigger.get_watchers())

                # perform config merging operations
                trigger.merge_cfg(self.urlhandlers)


# global config instance for the running kevin.
CFG = Config()
