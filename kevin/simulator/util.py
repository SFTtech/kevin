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
        while not (line and "HEAD" in line):
            data = yield from proc.stdout.readline()
            if not data:
                break
            line = data.decode('utf8').rstrip()

        if "HEAD" in line:
            head_hash = line.split()[0]

        yield from proc.wait()
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
