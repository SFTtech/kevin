"""
Utility function for the simulator
"""

import asyncio


def get_hash(repo):
    """
    return the head hash of the given repo.

    could be way more sophisticated to not only use
    HEAD but a branch instead if you need it.
    """

    @asyncio.coroutine
    def repo_query(repo):
        proc = yield from asyncio.create_subprocess_exec(
            "git", "ls-remote", repo,
            stdout=asyncio.subprocess.PIPE
        )

        line = None
        head_hash = None
        # find the "HEAD" output line
        while not line or "HEAD" not in line:
            data = yield from proc.stdout.readline()
            if not data:
                break
            line = data.decode('utf8').rstrip()

        retval = yield from proc.wait()

        if retval != 0:
            raise Exception("failed to determine HEAD hash in '%s'" % repo)

        if line and "HEAD" in line:
            head_hash = line.split()[0]

        return head_hash

    return repo_query(repo)


def update_server_info(repo):
    """
    call `git update-server-info` in the given repo.
    """

    @asyncio.coroutine
    def repo_update(repo):
        print("updating http server info in '%s'..." % repo)
        proc = yield from asyncio.create_subprocess_shell(
            "cd %s && git update-server-info" % repo
        )

        yield from proc.wait()

    return repo_update(repo)
