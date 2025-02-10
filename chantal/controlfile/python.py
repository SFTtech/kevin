from __future__ import annotations

from . import ControlFile, Step

import typing
if typing.TYPE_CHECKING:
    from pathlib import Path
    from .. import Args


class PythonControlFile(ControlFile):
    def __init__(self, file: Path, args: Args) -> None:
        super().__init__()

        # read and evaluate python control file
        raise NotImplementedError()

    def get_steps(self) -> list[Step]:
        raise NotImplementedError()
