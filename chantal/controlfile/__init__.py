"""
build job configuration
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ControlFile(ABC):
    def __init__(self) -> None:
        pass

    @abstractmethod
    def get_steps(self) -> list[Step]:
        raise NotImplementedError()


@dataclass
class Step:
    name: str
    depends: list[str]
    cwd: str | None
    commands: list[str]
    env: dict[str, str] = field(default_factory=dict)
    # output_source, output_destination
    outputs: list[tuple[str, str]] = field(default_factory=list)
    skip: bool = False
    hidden: bool = False


class ParseError(ValueError):
    """ Control file parsing error """
    def __init__(self, lineno, text):
        super().__init__(lineno + 1, text)
