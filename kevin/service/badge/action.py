"""
Status badge generation for a build.
"""

from __future__ import annotations

import enum
import logging
import os.path
import typing

from pathlib import Path

from .generator import BadgeGenerator

from ...action import Action
from ...config import CFG
from ...update import BuildState, BuildFinished
from ...watcher import Watcher


if typing.TYPE_CHECKING:
    from ...build import Build
    from ...project import Project
    from ...update import Update


class BadgeType(enum.Enum):
    success = enum.auto()
    fail = enum.auto()
    error = enum.auto()


class StatusBadge(Action):
    """
    GitHub status updater action, enable in a project to
    allow real-time build updates via the github api.
    """

    def __init__(self, cfg: dict[str, str], project: Project):
        super().__init__(cfg, project)
        base_text = cfg.get("base_text", "build status")
        texts = {
            BadgeType.success: ("green", cfg.get("success_text", "success")),
            BadgeType.fail: ("red", cfg.get("fail_text", "failed")),
            BadgeType.error: ("blue", cfg.get("error_text", "errored")),
        }

        # create all badge files in static output directory
        # each build then links to it
        self._badges_path = project.storage_path / 'badge'

        if CFG.volatile:
            return

        logging.debug('[status_badge] perparing badge files in %s', self._badges_path)
        self._badges_path.mkdir(parents=True, exist_ok=True)

        for badge_type, (colorscheme, text) in texts.items():
            gen = BadgeGenerator(base_text, text, right_color=colorscheme)
            badge_path = self._badges_path / f"{badge_type.name}.svg"
            with badge_path.open("w") as badge_file:
                badge_file.write(gen.get_svg())

    async def get_watcher(self, build: Build, completed: bool) -> Watcher | None:
        if completed:
            return None

        return _BadgeCreator(build, self)

class _BadgeCreator(Watcher):
    def __init__(self, build: Build, config: StatusBadge):
        self._build = build
        self._cfg = config

    async def on_update(self, update: Update) -> None:
        badge_type: BadgeType | None = None

        match update:
            case BuildState():
                if update.is_succeeded():
                    badge_type = BadgeType.success
                else:
                    match update.state:
                        case "failure":
                            badge_type = BadgeType.fail
                        case "error":
                            badge_type = BadgeType.error
                        case _:
                            return

            case BuildFinished():
                self._build.deregister_watcher(self)

            case _:
                return

        if badge_type is not None:
            self._link_badge(badge_type)

    def _link_badge(self, badgetype: BadgeType) -> None:
        try:  # try forming a relative path on the same FS
            # TODO python3.12 use relative_to(..., walk_up=True)
            badges_path = Path(os.path.relpath(self._cfg._badges_path.resolve(), self._build.path.resolve()))
        except ValueError:
            badges_path = self._cfg._badges_path

        build_badge = self._build.path / "status.svg"
        build_badge_tmp = build_badge.with_name(".status.svg.tmp")

        if CFG.volatile:
            logging.debug('[status_badge] would create %s badge as %s', badgetype.name, build_badge)
            return

        logging.debug('[status_badge] creating %s badge as %s', badgetype.name, build_badge)
        build_badge_tmp.symlink_to(
            badges_path / f"{badgetype.name}.svg"
        )
        build_badge_tmp.rename(build_badge)
