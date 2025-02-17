"""
Symlink management for the build.
Used to maintain branch pointers to commits.
"""

from __future__ import annotations

import logging
import os.path
import typing
from pathlib import Path

from ..action import Action
from ..config import CFG
from ..update import BuildSource, BuildState
from ..util import strlazy
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

        self._aliases: dict[str, str] = dict()
        for branch in cfg.get("alias", "").split(","):
            branch = branch.strip()
            if branch:
                if ":" not in branch:
                    raise ValueError(f"branch alias format is <branch_spec>:<alias>, missing in {branch!r}")
                branch, alias = branch.split(":", maxsplit=1)
                self._aliases[branch] = alias

        if self._only_branches:
            self._only_branches -= self._exclude_branches

        if CFG.volatile:
            return
        self.target_dir.mkdir(exist_ok=True, parents=True)

    def source_allowed(self, update: BuildSource) -> bool:
        if not (update.repo_id and update.branch):
            return False
        branch_id = f"{update.repo_id}/{update.branch}"
        if self._only_branches:
            return branch_id in self._only_branches

        return branch_id not in self._exclude_branches

    def get_allowed_branches_str(self) -> str:
        if self._only_branches:
            return f"only: {', '.join(self._only_branches)}"

        return f"not: {', '.join(self._exclude_branches)}"

    def get_alias(self, branch: str) -> str:
        alias = self._aliases.get(branch)
        return alias or branch

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
                    logging.debug("[symlink_branch] registering link source %s/%s", update.repo_id, update.branch)
                    self._build_sources.append(update)
                else:
                    logging.debug("[symlink_branch] ignoring link source %s/%s due to match rules: '%s'", update.repo_id, update.branch, strlazy(lambda: self._cfg.get_allowed_branches_str()))

            case BuildState():
                if update.is_completed():
                    for source in self._build_sources:
                        self._link_source(source)
            case _:
                pass

    def _link_source(self, source: BuildSource) -> None:
        """
        symlink platform/repo/branch from a build source to the build directory.
        """
        link_name = self._cfg.get_alias(f"{source.repo_id}/{source.branch}")

        link_path = self._cfg.target_dir / link_name
        if '..' in Path(link_name).parts:
            raise ValueError(f"[symlink_branch] branch link {link_name!r} contains '..', could be placed outside target directory")
        if not CFG.volatile:
            link_path.parent.mkdir(exist_ok=True, parents=True)

        tmp_branch_link = link_path.parent / f".{link_path.name}.tmp"

        try:
            # TODO python3.12 use relative_to(..., walk_up=True)
            build_path = Path(os.path.relpath(self._build.path.resolve(), link_path.parent))
        except ValueError:
            # absolute path needed for filesystem boundary
            build_path = self._build.path.resolve()

        if CFG.volatile:
            logging.debug("[symlink_branch] would link branch %s to %s", link_path, build_path)
            return

        logging.debug("[symlink_branch] linking branch %s to %s", link_path, build_path)
        # allow / in branch names
        link_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_branch_link.symlink_to(build_path)
        tmp_branch_link.rename(link_path)
