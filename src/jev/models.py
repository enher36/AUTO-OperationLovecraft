from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WindowSpec:
    title_contains: str
    width: int
    height: int


@dataclass(frozen=True)
class Target:
    name: str
    template: Path | None = None
    text: str | None = None
    region: tuple[int, int, int, int] | None = None
    threshold: float = 0.92


@dataclass(frozen=True)
class Action:
    kind: str  # click, press, wait
    target: str | None = None
    key: str | None = None


@dataclass(frozen=True)
class Step:
    id: str
    when: tuple[str, ...]
    action: Action
    success: tuple[str, ...]
    next_steps: tuple[str, ...] = ()
    timeout: float = 5.0
    max_attempts: int = 1
    repeat_safe: bool = False


@dataclass(frozen=True)
class Workflow:
    name: str
    window: WindowSpec
    targets: dict[str, Target]
    steps: dict[str, Step]
    completion: tuple[str, ...]
    source: Path
    fingerprint: str
    example: bool = False


@dataclass(frozen=True)
class WindowInfo:
    handle: int
    title: str
    left: int
    top: int
    width: int
    height: int
    foreground: bool = True
    minimized: bool = False


@dataclass(frozen=True)
class Frame:
    image: Any  # numpy BGR image; keep numpy out of configuration-only commands
    captured_at: float  # time.monotonic(), after the capture completes
    window: WindowInfo | None = None


@dataclass(frozen=True)
class Detection:
    name: str
    score: float
    center: tuple[int, int]
    text: str | None = None


@dataclass(frozen=True)
class Observation:
    frame: Frame
    detections: dict[str, Detection] = field(default_factory=dict)

    def matches(self, names: tuple[str, ...]) -> bool:
        return bool(names) and all(name in self.detections for name in names)


class JevError(Exception):
    """An actionable configuration, recognition, or runtime error."""


class WorkflowError(JevError):
    pass


class DesktopError(JevError):
    pass


class InputNotSent(DesktopError):
    """A desktop precondition failed before any input was dispatched."""
