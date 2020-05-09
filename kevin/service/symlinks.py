"""
Symlink management for the build.
Used to maintain branch pointers to commits.
"""

from ..action import Action
from ..watcher import Watcher


class SymlinkBranches(Action):
    """
    Manages branch symlinks in the output folder.
    """

    @classmethod
    def name(cls):
        return "symlink_branches"

    def __init__(self, cfg, project):
        super().__init__(cfg, project)

        branchdef = cfg.get("exclude")
        if branchdef:
            self.excluded_branches = [bra.strip()
                                      for bra in branchlist.split(",")]
        else:
            self.excluded_branches = set()

    def get_watcher(self, build, completed):
        return SymlinkCreator(build, self)


class SymlinkCreator(Watcher):
    """
    Watches for git source messages so it can create symlinks
    that link to the commit for that branch.
    """

    def __init__(self, build, config):
        self.build = build
        self.cfg = config

    async def on_update(self, update):
        # intercept the update from build.add_source()
        if isinstance(update, BuildSource):
            pass
