"""Deterministic synthetic UI, exercising real recognition without desktop input."""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import yaml

from .images import write_image
from .models import Action, Frame, JevError, Observation


class DemoDesktop:
    def __init__(self, screens: list, *, already_complete: bool = False, stuck: bool = False):
        self.screens = screens
        self.index = 2 if already_complete else 0
        self.stuck = stuck
        self.sent = []

    def capture(self) -> Frame:
        return Frame(self.screens[self.index].copy(), time.monotonic())

    def perform(self, action: Action, observation: Observation) -> None:
        expected = ["open_button", "claim_button"]
        if self.index >= 2 or action.kind != "click" or action.target != expected[self.index]:
            raise JevError("演示收到不符合当前画面的操作")
        self.sent.append(action.target)
        if not self.stuck:
            self.index += 1

    def release(self) -> None:
        pass

    def close(self) -> None:
        pass


def create_demo(directory: Path) -> tuple[Path, list]:
    directory.mkdir(parents=True, exist_ok=True)
    width, height = 640, 360
    names = ["home", "open_button", "daily", "claim_button", "done"]
    positions = [(24, 24), (270, 180), (24, 24), (270, 180), (280, 130)]
    templates = {}
    rng = np.random.default_rng(42)
    for name in names:
        # Unique textured patches avoid degenerate or repeated matching examples.
        template = rng.integers(30, 240, size=(40, 64, 3), dtype=np.uint8)
        templates[name] = template
        write_image(directory / "templates" / f"{name}.png", template)
    screens = []
    for index, (label, active) in enumerate([
        ("DEMO - HOME", ["home", "open_button"]),
        ("DEMO - DAILY", ["daily", "claim_button"]),
        ("DEMO - COMPLETE", ["done"]),
    ]):
        screen = np.full((height, width, 3), (25, 21, 18), dtype=np.uint8)
        cv2.putText(screen, label, (120, 85), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (220, 220, 220), 1)
        for name in active:
            x, y = positions[names.index(name)]
            screen[y:y + 40, x:x + 64] = templates[name]
        screens.append(screen)
        write_image(directory / "screens" / f"{index}.png", screen)
    config = {
        "version": 1, "name": "JEV 离线演示（合成界面）", "example": True,
        "window": {"title_contains": "JEV Synthetic Demo", "width": width, "height": height},
        "targets": {name: {"template": f"templates/{name}.png", "threshold": 0.98} for name in names},
        "completion": ["done"],
        "steps": [
            {"id": "open_daily", "when": ["home", "open_button"],
             "action": {"type": "click", "target": "open_button"}, "success": ["daily"],
             "next": ["claim"], "timeout": 0.5},
            {"id": "claim", "when": ["daily", "claim_button"],
             "action": {"type": "click", "target": "claim_button"}, "success": ["done"],
             "timeout": 0.5},
        ],
    }
    path = directory / "demo.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path, screens
