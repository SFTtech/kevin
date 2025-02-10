"""
Driver for building a job.
All output is reported via the output() module.
"""

from __future__ import annotations

import os
import shlex
import typing
from pathlib import Path
from time import time

from .controlfile import ControlFile
from .controlfile.python import PythonControlFile
from .controlfile.makeish import MakeishControlFile

from .msg import (
    job_state, step_state, stdout,
    output_item as msg_output_item,
    output_dir as msg_output_dir,
    output_file as msg_output_file,
    raw_msg
)
from .util import run_command, filter_t
from .error import CommandError, OutputError

if typing.TYPE_CHECKING:
    from . import Args
    from .controlfile import Step


def run_job(args: Args) -> None:
    """
    Main entry point for building a job.
    """
    base_env = os.environ.copy()
    base_env.update({
        "TERM": "xterm",
        "GCC_COLORS": "yes",
        "KEVIN": "true",
        "CHANTAL": "true",
        "KEVIN_JOB": args.job or "",
    })

    if args.clone_source:
        _clone_repo(args, base_env)
    elif args.work_location:
        os.chdir(args.work_location)

    control_file: ControlFile
    if args.format == "python":
        control_file = PythonControlFile(Path(args.filename), args)
    elif args.format == "makeish":
        control_file = MakeishControlFile(Path(args.filename), args)
    else:
        raise ValueError(f"unhandled control file format {args.control_file_format!r}")

    steps = control_file.get_steps()

    _process_steps(steps, base_env, args)


def _clone_repo(args: Args, env) -> None:
    job_state("running", "cloning repo")

    if args.fetch_depth > 0:
        shallow = ("--depth %d" % args.fetch_depth)
    else:
        shallow = None

    if args.treeish:
        refname = f"kevin-{args.branch.replace(':', '/')}" if args.branch else "kevin-build"

        # to silence main/master warnings
        run_command("git config --global init.defaultBranch kevin-build", env=env, hide_invoc=True)

        # don't clone, instead fetch from remote to select treeish
        run_command(f"git init '{args.work_location}'", env=env)
        os.chdir(args.work_location)
        run_command(f"git remote add origin {shlex.quote(args.clone_source)}", env=env)
        run_command(filter_t(("git", "fetch", shallow, "--no-tags", "--prune", "origin",
                                f"{args.treeish}:{refname}")),
                    env=env)
        run_command(f"git checkout -q '{refname}'", env=env)

    else:
        if args.branch:
            branch = f"--branch '{args.branch}' --single-branch"
        else:
            branch = None

        run_command(filter_t(("git clone", shallow, branch, shlex.quote(args.clone_source), args.work_location)), env=env)
        os.chdir(args.work_location)


def _process_steps(steps: list[Step], base_env: dict[str, str], args: Args) -> None:
    for step in steps:
        if not step.skip:
            step_state(step, "waiting", "waiting")

    jobtimer = time()

    # steps that errored
    errors: list[str] = []
    # steps that succeeded
    success: set[str] = set()

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

        steptimer = time()
        stdout("\n\x1b[36;1m[%s]\x1b[m\n" % step.name)

        try:
            step_env = base_env.copy()
            step_env.update(step.env)

            # execute commands
            for command in step.commands:
                run_command(command, env=step_env, cwd=step.cwd, shell=True)

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
                "completed in %.2f s" % (time() - steptimer)
            )
            success.add(step.name)

    if not errors:
        job_state("success", "completed in %.2f s" % (time() - jobtimer))


def output_item(source_name: str, output_name: str) -> None:
    """
    Outputs one output item, as listed in the config.
    """
    source_path = Path(source_name)

    # announce file or dir transfer
    if source_path.is_file():
        output_file(source_path, output_name)
    elif source_path.is_dir():
        output_dir(source_path, output_name)
    else:
        raise OutputError("non-existing output: %s" % source_path)

    # finalize the file transfer
    msg_output_item(output_name)


def output_file(path: Path, targetpath: str) -> None:
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
                data = b'\0' * chunksize

            raw_msg(data)
            remaining -= len(data)


def output_dir(path: Path, targetpath: str) -> None:
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
        else:
            pass
