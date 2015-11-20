#!/usr/bin/env python3

"""
Simulates the server-side pull request api.

Delivers a pull request hook to the specified url,
then waits for interaction.
"""

import argparse

from . import github


# available services to simulate
SERVICES = {
    "github": github.GitHub,
}


def main():
    cmd = argparse.ArgumentParser()
    cmd.add_argument("service", choices=SERVICES.keys(),
                     help="the service to simulate")
    cmd.add_argument("repo", help="clone url/path to the test repo")
    cmd.add_argument("config_file", help="config file of to-be-tested kevin")
    cmd.add_argument("-p", "--port", type=int, default=8423,
                     help="port to run the simulation on")
    cmd.add_argument("-l", "--listen", default="::1",
                     help="address to listen on for requests")

    args = cmd.parse_args()

    srv = SERVICES[args.service](args)
    srv.run()


if __name__ == "__main__":
    main()
