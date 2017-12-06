"""
Driver for building a job.
All output is reported via the output() module.
"""

import os
import pathlib
import shlex
from time import time

from .controlfile import parse_control_file, ParseError
from .msg import (
    job_state, step_state, stdout,
    output_item as msg_output_item,
    output_dir as msg_output_dir,
    output_file as msg_output_file,
    raw_msg
)
from .util import FatalBuildError, run_command, CommandError


class OutputError(Exception):
    """
    Raised when a file output fails.
    """
    pass


def build_job(args):
    """
    Main entry point for building a job.
    """
    base_env = os.environ.copy()
    base_env.update({
        "TERM": "xterm",
        "GCC_COLORS": "yes"
    })

    if args.clone_location:
        job_state("running", "cloning repo")

        if args.clone_depth > 0:
            shallow = ("--depth %d " % args.clone_depth)
        else:
            shallow = ""

        run_command("git clone " + shallow +
                    shlex.quote(args.clone_location) +
                    " " + args.work_location, base_env)

    os.chdir(args.work_location)

    if args.treeish:
        run_command("git checkout -q " + args.treeish, base_env)

    try:
        with open(args.filename) as controlfile:
            steps = parse_control_file(controlfile.read(), args)
    except FileNotFoundError:
        raise FatalBuildError(
            "no kevin config file named '%s' was found" % (args.filename))
    except ParseError as exc:
        raise FatalBuildError("%s:%d: %s" % (args.filename, exc.args[0],
                                             exc.args[1]))

    for step in steps:
        if not step.skip:
            step_state(step, "waiting", "waiting")

    errors = []
    success = set()
    for step in steps:
        depend_issues = set(step.depends) - success

        if step.skip:
            # the step has been marked to be skipped in the control file.
            # do not run it or produce any output.
            if not depend_issues:
                success.add(step.name)
            continue

        if not errors:
            job_state("running", "running (" + step.name + ")")

        if depend_issues:
            text = "depends failed: " + ", ".join(depend_issues)
            step_state(step, "error", text)
            stdout("\n\x1b[36;1m[%s]\x1b[m\n\x1b[31;1m%s\x1b[m\n" %
                   (step.name, text))
            continue

        if step.commands or step.outputs:
            step_state(step, "running", "running")

        timer = time()
        stdout("\n\x1b[36;1m[%s]\x1b[m\n" % step.name)

        try:
            step_env = base_env.copy()
            step_env.update(step.env)

            # execute commands
            for command in step.commands:
                run_command(command, step_env, step.cwd)

            # then, transfer output files
            for output_src, output_dst in step.outputs:
                output_item(output_src, output_dst)

        except (CommandError, OutputError) as exc:
            # failure in step command.
            step_state(step, "failure", str(exc.args[0]))

            if not step.hidden:
                errors.append(step.name)
                job_state(
                    "failure",
                    "steps failed: " + ", ".join(sorted(errors))
                )
        else:
            step_state(
                step, "success",
                "completed in %.2f seconds" % (time() - timer)
            )
            success.add(step.name)

    if not errors:
        job_state("success", "completed")


def output_item(source_name, output_name):
    """
    Outputs one output item, as listed in the config.
    """
    source_path = pathlib.Path(source_name)

    # announce file or dir transfer
    if source_path.is_file():
        output_file(source_path, output_name)
    elif source_path.is_dir():
        output_dir(source_path, output_name)
    else:
        raise OutputError("non-existing output: %s" % source_path)

    # finalize the file transfer
    msg_output_item(output_name)


def output_file(path, targetpath):
    """
    Outputs a single raw file. Temporarily switches the control stream
    to binary mode.
    """
    size = path.stat().st_size
    with path.open('rb') as fileobj:
        # change to binary mode
        msg_output_file(targetpath, size)

        remaining = size
        while remaining > 0:
            # read max 8MiB at once
            chunksize = min(remaining, 8 * 1024**2)
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
    msg_output_dir(targetpath)
    for entry in path.iterdir():
        entrytargetpath = targetpath + '/' + entry.name
        if entry.is_file():
            output_file(entry, entrytargetpath)
        elif entry.is_dir():
            output_dir(entry, entrytargetpath)
