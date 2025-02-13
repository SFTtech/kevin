"""
Utility function for the simulator
"""

import asyncio

from asyncio.subprocess import Process

from typing import Callable



async def get_hash(repo, branch_name: str | None = None) -> str:
    """
    return the commit hash of the given repo at HEAD or a branch name.
    """

    if branch_name:
        ref = f"refs/heads/{branch_name}"
    else:
        ref = "HEAD"

    ref_hash: str | None = None

    def line_handler(refhash: str, refname: str, proc: Process) -> bool:
        nonlocal ref_hash
        if ref == refname:
            ref_hash = refhash
            proc.terminate()
            return False
        return True

    await git_ls_remote(repo, ref, line_handler)

    if not ref_hash:
        raise Exception(f"could not find {ref!r} in repo {repo!r}")
    return ref_hash


async def get_refnames(repo: str, ref: str, only_branches: bool = False) -> list[str]:
    ref_names: list[str] = list()

    def line_handler(refhash: str, refname: str, proc: Process) -> bool:
        if ref == refhash and refname != "HEAD":
            ref_names.append(refname)
        return True

    await git_ls_remote(repo, None, line_handler)

    if only_branches:
        return [name.lstrip("refs/heads/")
                for name in ref_names
                if name.startswith("refs/heads/")]

    return ref_names


async def git_ls_remote(repo, ref: str | None = None,
                        line_func: Callable[[str, str, Process], bool] = lambda h, n, p: True) -> None:
    cmd = ["git", "ls-remote", repo] + ([ref] if ref else [])
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
    )

    if proc.stdout is None:
        raise Exception("stdout not captured")

    while True:
        data = await proc.stdout.readline()
        if not data:
            break
        line = data.decode('utf8').rstrip()
        refhash, refname = line.split()
        keep_looping = line_func(refhash, refname, proc)
        if not keep_looping:
            break

    retval = await proc.wait()

    if retval != 0:
        raise Exception(f"failed to determine {ref!r} hash in {repo!r}")


async def update_server_info(repo):
    """
    call `git update-server-info` in the given repo.
    """

    print("updating http server info in '%s'..." % repo)
    proc = await asyncio.create_subprocess_shell(
        "cd %s && git update-server-info" % repo
    )

    await proc.wait()
