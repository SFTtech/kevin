"""
Code for loading and parsing config options.
"""

from configparser import ConfigParser
import os
import pathlib


class Config:
    """ Global configuration for kevin. """
    def __init__(self):
        self.ci_name = None
        self.max_jobs_queued = None
        self.job_desc_file = None
        self.max_output = None
        self.job_timeout = None
        self.silence_timeout = None

        self.github_authtok = None
        self.github_hooksecret = None

        self.web_url = None
        self.web_folder = None
        self.dyn_port = None
        self.dyn_url = None

        self.falks = dict()

    def load(self, filename):
        """ Loads the attributes from the config file """
        raw = ConfigParser()
        raw.read(filename)

        try:
            self.ci_name = raw["kevin"]["name"]
            self.max_jobs_queued = int(raw["kevin"]["max_jobs_queued"])
            self.job_desc_file = raw["kevin"]["desc_file"]

            # TODO: size suffixes like M, G, T
            self.max_output = int(raw["kevin"]["max_output"])
            self.job_timeout = int(raw["kevin"]["job_timeout"])
            self.silence_timeout = int(raw["kevin"]["silence_timeout"])

            self.github_authtok = raw["github"]["user"], raw["github"]["token"]
            self.github_hooksecret = raw["github"]["hooksecret"].encode()

            self.web_url = raw["web"]["url"]
            self.web_folder = pathlib.Path(raw["web"]["folder"])
            self.dyn_port = int(raw["web"]["dyn_port"])
            self.dyn_url = raw["web"]["dyn_url"]

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

                self.falks[name] = dict(
                    user=user,
                    connection=connection,
                    location=location,
                )

        except KeyError as exc:
            print("\x1b[31mConfig file is missing entry: %s\x1b[m" % (exc))
            exit(1)

        self.verify()

    def verify(self):
        """ Verifies the validity of the loaded attributes """
        if not self.web_url.endswith('/'):
            raise ValueError("web URL must end in '/'")
        if not self.web_folder.is_dir():
            raise NotADirectoryError(str(self.web_folder))
        if not os.access(str(self.web_folder), os.W_OK):
            raise OSError("web folder is not writable")
        if not self.dyn_url.endswith('/'):
            raise ValueError("public status URL must end in '/'")


CFG = Config()
