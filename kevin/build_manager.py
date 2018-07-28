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

    async def new_build(self, project, commit_hash, create_new=True):
        """
        Create a new build or return it from the cache.
        """
        cache_key = (project, commit_hash)

        if cache_key in self.builds:
            # already known build.
            return self.builds[cache_key]

        else:
            # this tries loading from filesystem
            newbuild = Build(project, commit_hash)

            # try loading from filesystem
            await newbuild.load_from_fs()

            # store it as known build?
            if newbuild.completed or create_new:
                self.builds[cache_key] = newbuild
            else:
                newbuild = None

            return newbuild

    async def get_build(self, project, commit_hash):
        """
        Return an existing build from the cache.
        Return None if it coultn't be found.
        """

        return await self.new_build(project, commit_hash, create_new=False)
