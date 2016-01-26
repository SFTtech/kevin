"""
Code for loading and parsing config options.
"""

from collections import defaultdict
from configparser import ConfigParser
import os
import pathlib

from .project import Project
from .service import HookTrigger


class Config:
    """ Global configuration for kevin. """
    def __init__(self):
        self.ci_name = None
        self.max_jobs_queued = None

        self.web_url = None
        self.web_folder = None
        self.dyn_port = None
        self.dyn_url = None

        self.project_folder = None

        self.projects = dict()
        self.falks = dict()

        self.args = None

        self.urlhandlers = defaultdict(lambda: defaultdict(list))

    def set_cmdargs(self, args):
        """ Set runtime arguments """
        self.args = args

        if self.args.volatile:
            print("\x1b[1;31mYou are running in volatile mode, "
                  "nothing will be stored on disk!\x1b[m")

    def load(self, filename):
        """ Loads the attributes from the config file """
        raw = ConfigParser()
        raw.read(filename)

        try:
            cfglocation = pathlib.Path(filename).parent

            # main config
            kevin = raw["kevin"]
            self.ci_name = kevin["name"]
            self.max_jobs_queued = int(kevin["max_jobs_queued"])

            # project configurations.
            projects = raw["projects"]

            # for each project, there's a projname.conf in that folder
            projfolder = pathlib.Path(projects["config_folder"])
            if not projfolder.is_absolute():
                projfolder = cfglocation / projfolder

            if not projfolder.is_dir():
                raise NotADirectoryError(str(projfolder))

            self.project_folder = projfolder

            # TODO: maybe explicitly require file paths to be listed
            #       instead of iterating through all present files.
            for projectfile in self.project_folder.iterdir():
                if not str(projectfile).endswith(".conf"):
                    print("[projects] ignoring non .conf file '%s'" % (
                        projectfile))
                    continue

                # create the project
                newproj = Project(str(projectfile))
                if newproj.name in self.projects:
                    raise NameError("Project '%s' defined twice!" % (
                        newproj.name))

                print("[projects] loaded %s" % newproj.name)

                self.projects[newproj.name] = newproj

            # merge things required by projects
            self.project_postprocess()

            # web configuration
            web = raw["web"]
            self.web_url = web["url"]
            self.web_folder = pathlib.Path(web["folder"])
            self.dyn_port = int(web["dyn_port"])
            self.dyn_url = web["dyn_url"]

            if not self.web_folder.is_absolute():
                self.web_folder = cfglocation / self.web_folder

            # vm providers
            falk_entries = raw["falk"]
            for name, url in falk_entries.items():
                if name in self.falks:
                    raise ValueError("Falk double-defined: %s" % name)

                try:
                    user, target = url.split("@")
                except ValueError:
                    raise ValueError("%s=user@target malformed" % name)

                if ":" in target:
                    # ssh connection
                    host, port = target.split(":")
                    location = (host, port)
                    connection = "ssh"
                else:
                    # unix socket
                    location = target
                    connection = "unix"

                self.falks[name] = {
                    "user": user,
                    "connection": connection,
                    "location": location,
                }

        except KeyError as exc:
            print("\x1b[31mConfig file is missing entry: %s\x1b[m" % (exc))
            exit(1)

        self.verify()

    def verify(self):
        """
        Verifies the validity of the loaded attributes
        """
        if not self.web_url.endswith('/'):
            raise ValueError("web URL must end in '/': '%s'" % self.web_url)
        if not self.web_folder.is_dir():
            raise NotADirectoryError(str(self.web_folder))
        if not os.access(str(self.web_folder), os.W_OK):
            raise OSError("web folder is not writable")
        if not self.dyn_url.endswith('/'):
            raise ValueError("public status URL must end in '/'")

    def project_postprocess(self):
        """
        Postprocessing for all the project triggers/actions.

        Accross projects, configurations may need merging.
        Namely, if there's only one webhook handler for multiple projects,
        the configs need to be prepared for that.
        """
        # gather triggers to be installed.
        # TODO: relocate to service.py?
        # (handlerurl, handlerclass) -> {triggers: [cfg, cfg, ...]}
        for name, project in self.projects.items():
            # for each handler type (e.g. github webhook),
            # collect all the configs
            for trigger in project.triggers:
                if isinstance(trigger, HookTrigger):
                    # fetch the tornado request handler
                    handlerkwargs = self.urlhandlers[trigger.get_handler()]

                    # and add the config to it.
                    handlerkwargs["triggers"].append(trigger)
                else:
                    raise Exception("unknown trigger type %s" % trigger)


# global config instance for the running kevin.
CFG = Config()
