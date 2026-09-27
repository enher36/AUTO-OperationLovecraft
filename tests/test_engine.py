from dataclasses import replace
from pathlib import Path

import pytest

from jev.engine import Engine
from jev.models import (
    Action,
    Detection,
    Frame,
    JevError,
    Observation,
    Step,
    Target,
    WindowSpec,
    Workflow,
)


def observation(at: float, *names: str) -> Observation:
    return Observation(
        Frame(image=None, captured_at=at),
        {name: Detection(name, 1.0, (25, 25)) for name in names},
    )


@pytest.fixture
def workflow() -> Workflow:
    targets = {name: Target(name) for name in ("home", "open", "activity", "claim", "done")}
    steps = {
        "open": Step(
            "open", ("home",), Action("click", target="open"), ("activity",),
            next_steps=("claim",), timeout=5,
        ),
        "claim": Step(
            "claim", ("activity",), Action("click", target="claim"), ("done",),
            timeout=5,
        ),
    }
    return Workflow(
        "daily", WindowSpec("Game", 800, 600), targets, steps, ("done",),
        Path("daily.yaml"), "workflow-fingerprint",
    )


def retryable(workflow: Workflow) -> Workflow:
    steps = dict(workflow.steps)
    steps["open"] = replace(steps["open"], repeat_safe=True, max_attempts=2)
    return replace(workflow, steps=steps)


def test_actions_require_fresh_confirmation_before_next_step(workflow):
    engine = Engine(workflow)

    first = engine.tick(observation(1, "home", "open"), now=1)
    assert (first.kind, first.step_id, first.action) == (
        "action", "open", Action("click", target="open"),
    )
    engine.action_sent(1.2)
    assert engine.tick(observation(2, "activity", "claim"), now=2).kind == "verified"
    assert engine.confirmed == ["open"]
    assert engine.pending is None

    second = engine.tick(observation(3, "activity", "claim"), now=3)
    assert (second.kind, second.step_id) == ("action", "claim")
    engine.action_sent(3.2)
    assert engine.tick(observation(4, "done"), now=4).kind == "completed"
    assert engine.confirmed == ["open", "claim"]
    assert engine.attempts == {"open": 1, "claim": 1}
    assert engine.tick(observation(5, "home", "open"), now=5).kind == "completed"


def test_already_completed_task_is_skipped_without_an_action(workflow):
    engine = Engine(workflow)
    assert engine.tick(observation(1, "done"), now=1).kind == "skipped"
    assert not engine.attempts
    assert engine.tick(observation(2, "home", "open"), now=2).kind == "skipped"
    with pytest.raises(JevError):
        engine.resume(3)


@pytest.mark.parametrize("captured_at", [0.9, 1, 1.5])
@pytest.mark.parametrize("evidence", ["activity", "done"])
def test_pre_dispatch_or_equal_timestamp_frame_cannot_confirm(workflow, captured_at, evidence):
    engine = Engine(workflow)
    engine.tick(observation(1, "home", "open"), now=1)
    engine.action_sent(1.5)

    assert engine.tick(observation(captured_at, evidence), now=2).kind == "wait"
    assert engine.pending == "open"
    assert not engine.confirmed
    assert engine.tick(observation(captured_at, evidence), now=6.5).kind == "paused"
    assert engine.attempts == {"open": 1}


def test_loading_frames_do_not_repeat_input_before_timeout(workflow):
    engine = Engine(retryable(workflow))
    engine.tick(observation(1, "home", "open"), now=1)
    for at in (2, 3, 4, 5):
        assert engine.tick(observation(at), now=at).kind == "wait"
    assert engine.attempts == {"open": 1}
    assert engine.tick(observation(5.5, "activity"), now=5.5).kind == "verified"


def test_non_repeat_safe_action_pauses_on_timeout_even_with_attempts_left(workflow):
    steps = dict(workflow.steps)
    steps["open"] = replace(steps["open"], max_attempts=3)
    engine = Engine(replace(workflow, steps=steps))
    engine.tick(observation(1, "home", "open"), now=1)

    assert engine.tick(observation(6, "home", "open"), now=6).kind == "paused"
    assert engine.attempts == {"open": 1}
    assert engine.pending == "open"


def test_repeat_safe_retry_is_bounded_and_requires_source_state(workflow):
    engine = Engine(retryable(workflow))
    engine.tick(observation(1, "home", "open"), now=1)

    assert engine.tick(observation(6, "home", "open"), now=6).kind == "action"
    assert engine.attempts == {"open": 2}
    assert engine.tick(observation(11, "home", "open"), now=11).kind == "paused"
    assert engine.attempts == {"open": 2}


def test_repeat_safe_retry_pauses_if_source_state_is_unknown(workflow):
    engine = Engine(retryable(workflow))
    engine.tick(observation(1, "home", "open"), now=1)
    assert engine.tick(observation(6), now=6).kind == "paused"
    assert engine.attempts == {"open": 1}


def test_repeat_safe_retry_pauses_if_another_step_also_matches(workflow):
    workflow = retryable(workflow)
    steps = dict(workflow.steps)
    steps["other"] = Step("other", ("overlay",), Action("press", key="esc"), ("gone",))
    engine = Engine(replace(workflow, steps=steps))
    engine.tick(observation(1, "home", "open"), now=1)

    assert engine.tick(observation(6, "home", "open", "overlay"), now=6).kind == "paused"
    assert engine.attempts == {"open": 1}


def test_retry_does_not_click_a_target_that_disappeared(workflow):
    engine = Engine(retryable(workflow))
    engine.tick(observation(1, "home", "open"), now=1)
    assert engine.tick(observation(6, "home"), now=6).kind == "paused"
    assert engine.attempts == {"open": 1}


@pytest.mark.parametrize("names", [("home", "open", "activity", "claim"), (), ("home",)])
def test_ambiguous_unknown_or_missing_click_target_pauses_without_input(workflow, names):
    engine = Engine(workflow)
    assert engine.tick(observation(1, *names), now=1).kind == "paused"
    assert not engine.attempts


def test_contradictory_entry_and_success_evidence_pauses(workflow):
    steps = {"open": workflow.steps["open"]}
    engine = Engine(replace(workflow, steps=steps))
    assert engine.tick(observation(1, "home", "open", "activity"), now=1).kind == "paused"
    assert not engine.attempts


def test_terminal_step_success_requires_global_completion_evidence(workflow):
    steps = dict(workflow.steps)
    steps["claim"] = replace(steps["claim"], success=("claim_acknowledged",))
    engine = Engine(replace(workflow, steps=steps))
    engine.tick(observation(1, "activity", "claim"), now=1)

    assert engine.tick(observation(2, "claim_acknowledged"), now=2).kind == "paused"
    assert engine.status != "completed"
    assert engine.confirmed == ["claim"]


def test_resume_keeps_pending_unsafe_action_and_requires_new_evidence(workflow):
    engine = Engine(workflow)
    engine.tick(observation(1, "home", "open"), now=1)
    engine.pause("user pause")
    assert engine.tick(observation(2, "activity"), now=2).kind == "paused"
    engine.resume(10)

    assert engine.tick(observation(9, "activity"), now=10).kind == "wait"
    assert engine.tick(observation(11, "home", "open"), now=11).kind == "wait"
    assert engine.tick(observation(15, "home", "open"), now=15).kind == "paused"
    assert engine.attempts == {"open": 1}
    engine.resume(20)
    assert engine.tick(observation(21, "activity"), now=21).kind == "verified"
    assert engine.attempts == {"open": 1}


def test_checkpoint_keeps_pending_intent_without_replaying_input(workflow):
    engine = Engine(workflow)
    engine.tick(observation(100, "home", "open"), now=100)

    restored = Engine.restore(workflow, engine.snapshot(), now=1)
    assert restored.pending == "open"
    assert restored.attempts == {"open": 1}
    assert restored.tick(observation(1, "activity"), now=1).kind == "wait"
    assert restored.tick(observation(2, "activity"), now=2).kind == "verified"
    assert restored.attempts == {"open": 1}


@pytest.mark.parametrize("restore", [False, True])
def test_resume_preserves_allowed_next_steps(workflow, restore):
    steps = dict(workflow.steps)
    steps["open"] = replace(steps["open"], max_attempts=3)
    workflow = replace(workflow, steps=steps)
    engine = Engine(workflow)
    engine.tick(observation(1, "home", "open"), now=1)
    assert engine.tick(observation(2, "activity"), now=2).kind == "verified"
    engine.pause("user pause")

    if restore:
        engine = Engine.restore(workflow, engine.snapshot(), now=10)
    else:
        engine.resume(10)
    assert engine.allowed == ("claim",)
    assert engine.tick(observation(11, "home", "open"), now=11).kind == "paused"
    assert engine.attempts == {"open": 1}


def test_checkpoint_from_different_workflow_is_rejected(workflow):
    checkpoint = Engine(workflow).snapshot()
    with pytest.raises(JevError, match="流程或模板已改变"):
        Engine.restore(replace(workflow, fingerprint="different"), checkpoint, now=1)


@pytest.mark.parametrize("status", ["completed", "skipped"])
def test_finished_checkpoint_cannot_be_restored(workflow, status):
    checkpoint = Engine(workflow).snapshot()
    checkpoint["status"] = status
    with pytest.raises(JevError, match="已经完成"):
        Engine.restore(workflow, checkpoint, now=1)


@pytest.mark.parametrize(
    "changes",
    [
        {"attempts": {"open": True}},
        {"attempts": {"open": 0}},
        {"attempts": {"open": 2}},
        {"status": "waiting", "pending": None},
        {"pending": "open", "attempts": {}},
        {"allowed": ["missing"]},
    ],
)
def test_malformed_checkpoint_is_rejected(workflow, changes):
    checkpoint = Engine(workflow).snapshot()
    checkpoint.update(changes)
    with pytest.raises(JevError):
        Engine.restore(workflow, checkpoint, now=1)
