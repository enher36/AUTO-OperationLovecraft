"""Offline screenshot replay with a deterministic virtual clock."""

from pathlib import Path

from .images import read_image
from .models import Frame, JevError


class ReplayDesktop:
    def __init__(self, paths: list[Path]):
        self.paths = iter(paths)
        self.now = 1.0

    def clock(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def capture(self) -> Frame:
        try:
            path = next(self.paths)
        except StopIteration as exc:
            raise JevError("回放画面已用完，尚未确认任务完成") from exc
        return Frame(read_image(path), self.now)

    def perform(self, action, observation) -> None:
        pass  # The action is recorded by the runner; replay never sends desktop input.

    def release(self) -> None:
        pass

    def close(self) -> None:
        pass
