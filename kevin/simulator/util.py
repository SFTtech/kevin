"""
Utility function for the simulator
"""

import asyncio


async def get_hash(repo):
    """
    return the head hash of the given repo.

    could be way more sophisticated to not only use
    HEAD but a branch instead if you need it.
    """

    proc = await asyncio.create_subprocess_exec(
        "git", "ls-remote", repo,
        stdout=asyncio.subprocess.PIPE
    )

    head_hash = None

    # find the "HEAD" output line
    while True:
        data = await proc.stdout.readline()
        if not data:
            break
        line = data.decode('utf8').rstrip()
        if "HEAD" in line:
            head_hash = line.split()[0]
            proc.terminate()
            break

    retval = await proc.wait()

    if retval != 0:
        raise Exception("failed to determine HEAD hash in '%s'" % repo)

    if not head_hash:
        raise Exception("could not find HEAD in repo '%s'" % repo)

    return head_hash


async def update_server_info(repo):
    """
    call `git update-server-info` in the given repo.
    """

    print("updating http server info in '%s'..." % repo)
    proc = await asyncio.create_subprocess_shell(
        "cd %s && git update-server-info" % repo
    )

    await proc.wait()
