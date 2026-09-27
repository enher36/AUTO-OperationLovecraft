from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Action, JevError, Observation, Workflow


@dataclass(frozen=True)
class Decision:
    kind: str  # action, wait, verified, completed, skipped, paused
    reason: str
    step_id: str | None = None
    action: Action | None = None


class Engine:
    """Pure workflow state machine. Desktop input belongs to the runner."""

    def __init__(self, workflow: Workflow):
        self.workflow = workflow
        self.status = "ready"
        self.pending: str | None = None
        self.allowed = tuple(workflow.steps)
        self.attempts: dict[str, int] = {}
        self.confirmed: list[str] = []
        self.sent_at: float | None = None
        self.deadline: float | None = None
        self.reason = "等待识别当前状态"

    def pause(self, reason: str) -> Decision:
        self.status, self.reason = "paused", reason
        return Decision("paused", reason, self.pending)

    def resume(self, now: float) -> None:
        if self.status in {"completed", "skipped"}:
            raise JevError("这次运行已经完成，请启动一次新运行。")
        self.status = "waiting" if self.pending else "ready"
        self.reason = "重新识别当前状态"
        if self.pending:
            # Monotonic timestamps must not be persisted across restarts/reboots.
            self.action_sent(now)

    def action_sent(self, now: float) -> None:
        if not self.pending:
            raise JevError("没有待确认的操作。")
        self.sent_at = now
        self.deadline = now + self.workflow.steps[self.pending].timeout

    def _issue(self, step_id: str, now: float) -> Decision:
        step = self.workflow.steps[step_id]
        count = self.attempts.get(step_id, 0)
        if count >= step.max_attempts:
            return self.pause(f"步骤 {step_id} 已达到操作次数上限")
        self.pending = step_id
        self.attempts[step_id] = count + 1
        self.status = "waiting"
        # Record intent before dispatch: a crash at the input boundary is uncertain.
        self.action_sent(now)
        self.reason = f"执行步骤 {step_id}，第 {count + 1} 次尝试"
        return Decision("action", self.reason, step_id, step.action)

    def tick(self, observation: Observation, now: float) -> Decision:
        if self.status in {"paused", "completed", "skipped"}:
            return Decision(self.status, self.reason, self.pending)
        if self.pending and (
            self.sent_at is None or observation.frame.captured_at <= self.sent_at
        ):
            if self.deadline is not None and now >= self.deadline:
                return self.pause("未能获取操作之后的新画面")
            return Decision("wait", "等待操作之后的新画面", self.pending)

        if observation.matches(self.workflow.completion):
            self.status = "completed" if self.attempts else "skipped"
            if self.pending:
                self.confirmed.append(self.pending)
            self.pending = None
            self.reason = "已识别任务完成证据" if self.attempts else "任务已完成，本次跳过"
            return Decision(self.status, self.reason)

        if self.pending:
            step = self.workflow.steps[self.pending]
            if observation.matches(step.success):
                step_id = self.pending
                self.confirmed.append(step_id)
                self.pending = None
                self.status = "ready"
                self.allowed = step.next_steps
                self.sent_at = self.deadline = None
                self.reason = f"步骤 {step_id} 的结果已确认"
                # Success of a last step must also prove overall completion.
                if not self.allowed:
                    return self.pause(f"步骤 {step_id} 已确认，但缺少任务整体完成证据")
                return Decision("verified", self.reason, step_id)
            if self.deadline is not None and now < self.deadline:
                return Decision("wait", "等待操作结果", step.id)
            if (
                step.repeat_safe
                and self.attempts[step.id] < step.max_attempts
                and observation.matches(step.when)
                and self._matching_steps(observation, tuple(self.workflow.steps)) == [step.id]
            ):
                if step.action.kind == "click" and step.action.target not in observation.detections:
                    return self.pause(f"步骤 {step.id} 的点击目标未识别到，不能重试")
                return self._issue(step.id, now)
            return self.pause(f"步骤 {step.id} 超时，操作结果未确认")

        candidates = self._matching_steps(observation, self.allowed)
        if not candidates:
            return self.pause("当前画面不符合允许执行的步骤，也未识别到完成证据")
        if len(candidates) > 1:
            return self.pause("多个流程步骤同时匹配：" + ", ".join(candidates))
        step = self.workflow.steps[candidates[0]]
        if observation.matches(step.success):
            return self.pause(f"步骤 {step.id} 的进入条件与成功条件同时满足，请检查流程")
        if step.action.kind == "click" and step.action.target not in observation.detections:
            return self.pause(f"步骤 {step.id} 的点击目标未识别到")
        return self._issue(step.id, now)

    def _matching_steps(self, observation: Observation, allowed: tuple[str, ...]) -> list[str]:
        return [name for name in allowed if observation.matches(self.workflow.steps[name].when)]

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 1,
            "workflow": self.workflow.fingerprint,
            "status": self.status,
            "pending": self.pending,
            "allowed": list(self.allowed),
            "attempts": self.attempts.copy(),
            "confirmed": self.confirmed.copy(),
            "reason": self.reason,
        }

    @classmethod
    def restore(cls, workflow: Workflow, data: dict[str, Any], now: float) -> Engine:
        try:
            if data["version"] != 1 or data["workflow"] != workflow.fingerprint:
                raise ValueError("流程或模板已改变，请启动新运行")
            engine = cls(workflow)
            status, pending = data["status"], data["pending"]
            if status not in {"ready", "waiting", "paused", "completed", "skipped"}:
                raise ValueError("无效的运行状态")
            if status in {"completed", "skipped"}:
                raise ValueError("这次运行已经完成，请启动新运行")
            if pending is not None and pending not in workflow.steps:
                raise ValueError("未知的待确认步骤")
            attempts, allowed, confirmed = data["attempts"], data["allowed"], data["confirmed"]
            if not isinstance(attempts, dict) or any(
                name not in workflow.steps or type(count) is not int
                or not 1 <= count <= workflow.steps[name].max_attempts
                for name, count in attempts.items()
            ):
                raise ValueError("无效的操作次数")
            if pending is not None and pending not in attempts:
                raise ValueError("待确认操作缺少次数记录")
            if status == "waiting" and pending is None:
                raise ValueError("待确认状态缺少步骤")
            for names in (allowed, confirmed):
                if not isinstance(names, list) or any(
                    not isinstance(name, str) or name not in workflow.steps for name in names
                ):
                    raise ValueError("无效的步骤记录")
            engine.status, engine.pending = status, pending
            engine.attempts, engine.allowed = attempts.copy(), tuple(allowed)
            engine.confirmed = confirmed.copy()
            engine.resume(now)
            return engine
        except (KeyError, TypeError, ValueError) as exc:
            raise JevError(f"无法恢复运行记录：{exc}") from exc
