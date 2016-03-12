#!/usr/bin/env python3

from distutils.core import setup

setup(
    name="kevin",
    version="0.1",
    description="Self-hostable continuous integration toolkit",
    long_description=(
        "Components for running a continuous integration service "
        "right on your own servers.\n"
        "Kevin interacts with the Internet, falk manages the virtual machines "
        "and containers, chantal executes your build jobs."
    ),
    maintainer="SFT Technologies",
    maintainer_email="jj@stusta.net",
    url="https://github.com/SFTtech/kevin",
    license='AGPL3+',
    packages=[
        "kevin",
        "kevin.action",
        "kevin.project",
        "kevin.service",
        "kevin.simulator",
        "chantal",
        "falk",
        "falk.vm",
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
    ],
)
