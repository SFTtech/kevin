#!/usr/bin/env python3

from distutils.core import setup
import os
import glob


setup(
    name="kevin",
    version="0.5",
    description="Self-hostable continuous integration toolkit",
    long_description=(
        "Components for running a continuous integration service "
        "right on your own servers.\n"
        "Kevin interacts with the Internet, falk manages the virtual machines "
        "and containers, chantal executes your build jobs.\n\n"
        "It's designed to interact with a code hosting platform like GitHub "
        "but can easily be extended for others.\n"
        "Pull requests are build in temporary containers that are deleted"
        "after execution."
    ),
    maintainer="SFT Technologies",
    maintainer_email="jj@stusta.net",
    url="https://github.com/SFTtech/kevin",
    license='AGPL3+',
    packages=[
        "kevin",
        "kevin.service",
        "kevin.simulator",
        "chantal",
        "falk",
        "falk.vm",
    ],
    data_files=[
        ("/usr/lib/systemd/system/", [
            "etc/kevin.service",
            "etc/falk.service",
        ]),
        ("/usr/lib/tmpfiles.d", [
            "etc/tmpfiles.d/kevin.conf",
        ]),
        ("/etc/kevin", [
            "etc/kevinfile.example",
            "etc/kevin.conf.example",
            "etc/falk.conf.example",
        ]),
        ("/etc/kevin/projects", [
            "etc/project.conf.example",
        ]),
        ("/usr/share/webapps/mandy",
         glob.glob(os.path.join(os.path.dirname(__file__), "mandy/*"))),
    ],
    platforms=[
        'Linux',
    ],
    classifiers=[
        ("License :: OSI Approved :: "
         "GNU Affero General Public License v3 or later (AGPLv3+)"),
        "Topic :: Software Development :: Build Tools",
        "Topic :: Software Development :: Testing",
        "Topic :: Software Development :: Quality Assurance",
        "Topic :: Software Development :: Version Control",
        "Topic :: Internet :: WWW/HTTP",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "Environment :: Web Environment",
        "Environment :: Console",
        "Operating System :: POSIX :: Linux"
    ],
)
