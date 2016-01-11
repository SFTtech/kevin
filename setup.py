#!/usr/bin/env python3

from distutils.core import setup

setup(
    name="kevin",
    version="1.0",
    description="Self-hostable continuous integration toolkit",
    author="SFT Technologies",
    author_email="michael@ensslin.cc",
    url="https://github.com/SFTtech/kevin",
    packages=["kevin",
              "chantal",
              "falk"],
)
