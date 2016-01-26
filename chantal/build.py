"""
Driver for building a job.
All output is reported via the output() module.
"""

import os
import pathlib
import shlex
from time import time

from .controlfile import parse_control_file, ParseError
from .msg import msg, raw_msg
from .util import run_command, FatalBuildError


def update_step_status(step, state, text):
    """
    updates the status for the given step.
    does not send the update if the step is hidden.
    """
    if step.hidden:
        return

    msg(cmd="step-state", step=step.name, state=state, text=text)


def build_job(args):
    """
    Main entry point for building a job.
    """
    msg(cmd="job-state", state="pending", text="cloning repo")

    shallow = ("--depth %d " % args.shallow) if args.shallow > 0 else ""

    run_command("git clone " + shallow + shlex.quote(args.clone_url) + " repo")
    os.chdir("repo")
    run_command("git checkout -q " + args.commit_sha)

    try:
        with open(args.desc_file) as controlfile:
            steps = parse_control_file(controlfile.read())
    except FileNotFoundError:
        raise FatalBuildError(
            "no kevin config file named '%s' was found" % (args.desc_file))
    except ParseError as exc:
        raise FatalBuildError("%s:%d: %s" % (args.desc_file, exc.args[0],
                                             exc.args[1]))

    for step in steps:
        update_step_status(step, "pending", "waiting")

    errors = []
    success = set()
    for step in steps:
        print("\n\x1b[36;1m[%s]\x1b[m" % step.name)

        if not errors:
            msg(
                cmd="job-state",
                state="pending",
                text="running (" + step.name + ")"
            )

        depend_issues = set(step.depends) - success

        if depend_issues:
            text = "depends failed: " + ", ".join(depend_issues)
            update_step_status(step, "failure", text)
            print("skipping because " + text)
            continue

        if step.commands or step.outputs:
            update_step_status(step, "pending", "running")
        timer = time()

        try:
            for command in step.commands:
                run_command(command, step.env)
            for output in step.outputs:
                output_item(output)
        except RuntimeError as exc:
            # failure in step.
            update_step_status(step, "failure", str(exc.args[0]))

            if not step.hidden:
                errors.append(step.name)
                msg(
                    cmd="job-state",
                    state="failure",
                    text="steps failed: " + ", ".join(sorted(errors))
                )
        else:
            update_step_status(
                step, "success",
                "completed in %.2f seconds" % (time() - timer)
            )
            success.add(step.name)

    if not errors:
        msg(cmd="job-state", state="success", text="completed")


def output_item(path):
    """
    Outputs one output item, as listed in the config.
    """
    path = pathlib.Path(path)
    if path.is_file():
        output_file(path, path.name)
    elif path.is_dir():
        output_dir(path, path.name)
    else:
        raise RuntimeError("non-existing output: " + str(path))

    msg(cmd="output-item", name=path.name)


def output_file(path, targetpath):
    """
    Outputs a single raw file. Temporarily switches the control stream
    to binary mode.
    """
    size = path.stat().st_size
    with path.open('rb') as fileobj:
        msg(cmd="output-file", path=targetpath, size=size)
        remaining = size
        while remaining:
            chunksize = min(remaining, 8388608)  # read max 8MiB at once
            data = fileobj.read(chunksize)
            if not data:
                # the file size has changed... but we promised to deliver!
                data = '\0' * chunksize

            raw_msg(data)
            remaining -= len(data)


def output_dir(path, targetpath):
    """
    Recursively outputs a directory.
    """
    msg(cmd="output-dir", path=targetpath)
    for entry in path.iterdir():
        entrytargetpath = targetpath + '/' + entry.name
        if entry.is_file(targetpath):
            output_file(entry, entrytargetpath)
        elif entry.is_dir(targetpath):
            output_dir(entry, entrytargetpath)
