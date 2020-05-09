"""
Build caching and creation.
"""

from .build import Build


class BuildManager:
    """
    Manages which builds are in-memory.
    """

    def __init__(self):
        # stores known builds by (project, hash) -> build
        self.builds = dict()

    async def new_build(self, project, commit_hash, create_new=True,
                        force_rebuild=False):
        """
        Create a new build or return it from the cache.
        """
        cache_key = (project, commit_hash)

        cached_build = self.builds.get(cache_key)
        if cached_build and not force_rebuild:
            return cached_build

        else:
            # this tries loading from filesystem
            newbuild = Build(project, commit_hash)

            if force_rebuild:
                if cached_build:
                    del self.builds[cache_key]
            else:
                # try loading from filesystem
                await newbuild.load_from_fs()

            # store build to the cache
            # newbuild.completed => it could be loaded from fs
            # create_new => as its a new build, always store it
            if newbuild.completed or create_new:
                self.builds[cache_key] = newbuild
            else:
                # if the build couldn't be loaded from fs
                # and it's not a new build
                newbuild = None

            return newbuild

    async def get_build(self, project, commit_hash):
        """
        Return an existing build from the cache.
        Return None if it coultn't be found.
        """

        return await self.new_build(project, commit_hash, create_new=False)
