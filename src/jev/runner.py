from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .engine import Engine
from .images import write_image
from .models import InputNotSent, JevError, Workflow


def _report_payload(workflow, engine, status, reason, directory, warnings=()) -> dict:
    payload = {
        "workflow": workflow.name,
        "status": status,
        "reason": reason,
        "confirmed_steps": engine.confirmed.copy(),
        "pending_step": engine.pending,
        "attempts": engine.attempts.copy(),
        "unexecuted_steps": [name for name in workflow.steps if name not in engine.attempts],
        "directory": str(directory),
    }
    if warnings:
        payload["warnings"] = list(warnings)
    return payload


class Journal:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or Path("runs") / (
            datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6]
        )
        self.directory = self.directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index = 0

    def event(self, kind: str, **data: Any) -> None:
        payload = {"time": datetime.now().astimezone().isoformat(), "event": kind, **data}
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def checkpoint(self, engine: Engine) -> None:
        self._json("checkpoint.json", engine.snapshot())

    def _json(self, filename: str, payload: Any) -> None:
        path = self.directory / filename
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def frame(self, frame, label: str) -> str:
        self.index += 1
        filename = f"{time.time_ns()}-{self.index:04d}-{label}.png"
        write_image(self.directory / filename, frame.image)
        return filename

    def report(self, workflow: Workflow, engine: Engine, status: str, reason: str, warnings=()) -> dict:
        payload = _report_payload(workflow, engine, status, reason, self.directory, warnings)
        self._json("report.json", payload)
        return payload


def run_workflow(
    workflow: Workflow,
    capture,
    actions,
    recognizer,
    journal: Journal,
    *,
    engine: Engine | None = None,
    pause_hotkey=None,
    preview: bool = False,
    poll_interval: float = 0.2,
    max_seconds: float = 300,
    clock=time.monotonic,
    sleep=time.sleep,
) -> dict:
    engine = engine or Engine(workflow)
    last_frame = None
    last_message = None
    rollback_snapshot = None
    input_started = False
    warnings = []
    status, reason = "paused", "运行停止"

    def pause_requested() -> bool:
        return pause_hotkey is not None and pause_hotkey.is_pressed()

    try:
        started = clock()
        journal.event("run_started", workflow=workflow.name, preview=preview)
        while True:
            if pause_requested():
                decision = engine.pause("用户按下 F8 暂停")
            elif clock() - started >= max_seconds:
                decision = engine.pause("达到本次运行时长上限")
            else:
                last_frame = capture.capture()
                observation = recognizer.observe(last_frame)
                if pause_requested():
                    decision = engine.pause("用户按下 F8 暂停")
                else:
                    before_tick = engine.snapshot()
                    decision = engine.tick(observation, clock())
                    if decision.kind == "action":
                        rollback_snapshot = before_tick
                        input_started = False

            if decision.reason != last_message:
                print(decision.reason, flush=True)
                last_message = decision.reason

            if decision.kind == "action":
                evidence = journal.frame(last_frame, "before")
                journal.event(
                    "action_planned", step=decision.step_id,
                    action=asdict(decision.action), frame=evidence,
                    detections={key: asdict(value) for key, value in observation.detections.items()},
                )
                if preview:
                    # A preview never sends input and must not become a resumable pending action.
                    journal.event("preview_finished", step=decision.step_id)
                    status, reason = "preview", decision.reason
                    break
                # Persist intent before input, even if interrupted between these operations.
                journal.checkpoint(engine)
                if pause_requested():
                    raise InputNotSent("用户按下 F8 暂停，操作尚未发送")
                input_started = True
                try:
                    actions.perform(decision.action, observation)
                except InputNotSent:
                    input_started = False
                    raise
                rollback_snapshot = None
                engine.action_sent(clock())
                journal.checkpoint(engine)
                journal.event("action_sent", step=decision.step_id)

            if decision.kind == "verified":
                evidence = journal.frame(last_frame, "verified")
                journal.event("result_confirmed", step=decision.step_id, frame=evidence)
                journal.checkpoint(engine)

            if decision.kind in {"completed", "skipped", "paused"}:
                status, reason = decision.kind, decision.reason
                break
            sleep(poll_interval)
    except KeyboardInterrupt:
        reason = "用户中断运行"
    except Exception as exc:
        reason = str(exc) if isinstance(exc, JevError) else f"{type(exc).__name__}: {exc}"
    finally:
        # Release only input owned by the adapter, even after an interrupted press.
        cleanup = [("释放输入", actions.release), ("关闭截图", capture.close)]
        if pause_hotkey is not None:
            cleanup.append(("关闭暂停热键", pause_hotkey.close))
        for label, close in cleanup:
            try:
                close()
            except Exception as exc:
                warnings.append(f"{label}失败: {exc}")

    if rollback_snapshot is not None and not input_started:
        # The input boundary was never crossed; preserve prior attempts and path.
        engine = Engine.restore(workflow, rollback_snapshot, clock())
    if status == "paused":
        engine.pause(reason)

    def record(label, function):
        try:
            return function()
        except Exception as exc:
            warnings.append(f"{label}失败: {exc}")
            return None

    record("保存检查点", lambda: journal.checkpoint(engine))
    evidence = record("保存现场截图", lambda: journal.frame(last_frame, status)) if last_frame is not None else None
    record("保存结束事件", lambda: journal.event(status, reason=reason, frame=evidence))
    report = record("保存报告", lambda: journal.report(workflow, engine, status, reason, warnings))
    if report is None:
        report = _report_payload(workflow, engine, status, reason, journal.directory, warnings)
    return report
