"""
Build caching and creation.
"""

from __future__ import annotations

import typing

from .build import Build
from .lrustore import LRUStore

if typing.TYPE_CHECKING:
    from .project import Project
    from typing import Callable

    # (project, commit_hash)
    build_id_t = tuple[Project, str]


class BuildManager:
    """
    Manages which builds are in-memory.
    """

    def __init__(self, max_cached: int) -> None:
        def delete_check(build: Build, deleter: Callable[[], None]) -> bool:
            if not build.completed:
                build.call_on_complete(deleter)
                return False
            return True

        def revive(build: Build):
            build.call_on_complete(None)

        # stores known builds by (project, hash) -> build
        self._builds: LRUStore[build_id_t, Build] = LRUStore(max_size=max_cached,
                                                             max_killmap_size=10,
                                                             delete_check=delete_check,
                                                             revive=revive)

    async def _load_or_create_build(self,
                                    project: Project,
                                    commit_hash: str,
                                    create_new: bool,
                                    force_rebuild: bool = False) -> Build | None:
        cache_key: build_id_t = (project, commit_hash)

        cached_build = self._builds.get(cache_key)
        if cached_build and not force_rebuild:
            return cached_build

        newbuild = Build(project, commit_hash)

        if force_rebuild:
            if cached_build:
                del self._builds[cache_key]
        else:
            # try loading from filesystem
            await newbuild.load()

        # store build to the cache
        # newbuild.completed => it could be loaded from fs
        # create_new => as its a new build, always store it
        # force_rebuild => we deleted it before
        if newbuild.completed or create_new:
            self._builds[cache_key] = newbuild
        else:
            # if the build couldn't be loaded from fs
            # and it's not a new build
            return None

        return newbuild

    async def new_build(self, project: Project, commit_hash: str,
                        force_rebuild: bool = False) -> Build:
        """
        Create a new build or return it from the cache.
        """
        ret = await self._load_or_create_build(project, commit_hash,
                                               create_new=True, force_rebuild=force_rebuild)
        assert ret is not None
        return ret

    async def get_build(self, project: Project, commit_hash: str):
        """
        Return an existing build from the cache or a completed build from the filessystem.
        Return None if it coultn't be found or it's not complete.
        """

        return await self._load_or_create_build(project, commit_hash, create_new=False)
