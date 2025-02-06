"""
Symlink management for the build.
Used to maintain branch pointers to commits.
"""

from __future__ import annotations

import logging
import typing
from pathlib import Path

from ..action import Action
from ..config import CFG
from ..update import BuildSource, BuildState
from ..watcher import Watcher

if typing.TYPE_CHECKING:
    from ..build import Build
    from ..project import Project


class SymlinkBranch(Action):
    """
    Manages branch symlinks in the output folder.
    """

    def __init__(self, cfg: dict[str, str], project: Project) -> None:
        super().__init__(cfg, project)

        target_dir_raw = cfg.get("target_dir", "branches/")
        self.target_dir: Path = project.storage_path / Path(target_dir_raw)
        # exclude project job output dir
        if str(self.target_dir.resolve()).startswith(str((project.storage_path / "jobs").resolve())):
            raise ValueError("[symlink_branch] 'target_dir' clashes with build job directory:"
                             f"{self.target_dir}")

        self._only_branches: set[str] = set()
        for branch in cfg.get("only", "").split(","):
            branch = branch.strip()
            if branch:
                self._only_branches.add(branch)

        self._exclude_branches: set[str] = set()
        for branch in cfg.get("exclude", "").split(","):
            branch = branch.strip()
            if branch:
                self._exclude_branches.add(branch)

        if CFG.volatile:
            return
        self.target_dir.mkdir(exist_ok=True, parents=True)

    def _check_branch(self, branch_name: str) -> bool:
        if self._only_branches:
            only_branches = self._only_branches - self._exclude_branches
            return branch_name in only_branches

        return branch_name not in self._exclude_branches

    def source_allowed(self, update: BuildSource) -> bool:
        # TODO also check for update.repo_name maybe?
        return self._check_branch(update.branch)

    async def get_watcher(self, build, completed) -> Watcher | None:
        if not completed:
            return SymlinkCreator(build, self)
        return None


class SymlinkCreator(Watcher):
    """
    Watches for git source messages so it can create symlinks
    that link to the commit for that branch.
    """

    def __init__(self, build: Build, config: SymlinkBranch):
        self._build = build
        self._cfg = config

        # build sources
        self._build_sources: list[BuildSource] = list()

    async def on_update(self, update):
        if update is StopIteration:
            return

        match update:
            case BuildSource():
                if self._cfg.source_allowed(update):
                    self._build_sources.append(update)

            case BuildState():
                if update.is_completed():
                    for source in self._build_sources:
                        self._link_source(source)
            case _:
                pass

    def _link_source(self, source: BuildSource) -> None:
        """
        link a build source to a build
        for now just use the branch name.
        TODO: also use repo?
        """
        branch_name = source.branch

        branch_link = (self._cfg.target_dir / branch_name).resolve()
        if self._cfg.target_dir.resolve() not in branch_link.parents:
            # if ../ is in branch name...
            raise ValueError(f"[symlink_branch] branch link would be placed outside target directory: {branch_name!r}")

        tmp_branch_link = branch_link.parent / f".{branch_link.name}.tmp"

        try:
            build_path = self._build.path.resolve().relative_to(branch_link.parent, walk_up=True)
        except ValueError:
            # absolute path needed for filesystem boundary
            build_path = self._build.path.resolve()

        if CFG.volatile:
            logging.debug("[symlink_branch] would link branch %s to %s", branch_link, build_path)
            return

        logging.debug("[symlink_branch] linking branch %s to %s", branch_link, build_path)
        # allow / in branch names
        branch_link.parent.mkdir(parents=True, exist_ok=True)
        tmp_branch_link.symlink_to(build_path)
        tmp_branch_link.rename(branch_link)
