import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from jev.engine import Engine
from jev.models import Action, DesktopError, Detection, Frame, InputNotSent, Observation, Step, WindowSpec, Workflow
from jev.runner import Journal, run_workflow


@pytest.fixture
def setup(tmp_path):
    workflow = Workflow(
        "daily", WindowSpec("Game", 2, 2), {},
        {"open": Step("open", ("home",), Action("press", key="enter"), ("done",))},
        ("done",), Path("daily.yaml"), "fingerprint",
    )
    observation = Observation(
        Frame(np.zeros((2, 2, 3), dtype=np.uint8), 1),
        {"home": Detection("home", 1.0, (1, 1))},
    )
    capture = SimpleNamespace(capture=Mock(return_value=observation.frame), close=Mock())
    actions = SimpleNamespace(perform=Mock(), release=Mock())
    recognizer = SimpleNamespace(observe=Mock(return_value=observation))
    hotkey = SimpleNamespace(is_pressed=Mock(return_value=False), close=Mock())
    journal = Journal(tmp_path)
    return SimpleNamespace(workflow=workflow, capture=capture, actions=actions,
                           recognizer=recognizer, hotkey=hotkey, journal=journal)


def run(state, **kwargs):
    return run_workflow(state.workflow, state.capture, state.actions, state.recognizer, state.journal,
                        pause_hotkey=state.hotkey, clock=lambda: 1.0, sleep=lambda _: None, **kwargs)


def checkpoint(state):
    return json.loads((state.journal.directory / "checkpoint.json").read_text(encoding="utf-8"))


def report(state):
    return json.loads((state.journal.directory / "report.json").read_text(encoding="utf-8"))


def test_f8_before_sending_rolls_back_attempt_and_resume_can_proceed(setup, monkeypatch):
    original = setup.journal.checkpoint

    def request_pause(engine):
        original(engine)
        setup.hotkey.is_pressed.return_value = True

    monkeypatch.setattr(setup.journal, "checkpoint", request_pause)
    result = run(setup)
    assert result["status"] == "paused"
    assert result["attempts"] == {}
    assert result["pending_step"] is None
    setup.actions.perform.assert_not_called()
    restored = Engine.restore(setup.workflow, checkpoint(setup), now=10)
    observation = setup.recognizer.observe.return_value
    assert restored.tick(observation, now=11).kind == "action"


def test_precondition_failure_spends_no_attempts(setup):
    setup.actions.perform.side_effect = InputNotSent("Game window lost focus")
    result = run(setup)
    assert result["status"] == "paused"
    assert result["attempts"] == {}
    assert result["pending_step"] is None
    assert checkpoint(setup)["attempts"] == {}


@pytest.mark.parametrize("failure", [DesktopError("Input interrupted"), RuntimeError("Input uncertain"), KeyboardInterrupt()])
def test_failure_at_real_input_boundary_retains_pending_intent(setup, failure):
    setup.actions.perform.side_effect = failure
    result = run(setup)
    assert result["status"] == "paused"
    assert result["attempts"] == {"open": 1}
    assert result["pending_step"] == "open"
    assert checkpoint(setup)["pending"] == "open"


def test_intent_is_on_disk_before_input(setup):
    def fail_after_reading_intent(*_):
        data = checkpoint(setup)
        assert data["pending"] == "open"
        assert data["attempts"] == {"open": 1}
        raise DesktopError("Input uncertain")

    setup.actions.perform.side_effect = fail_after_reading_intent
    run(setup)


def test_recognition_exception_saves_paused_report(setup):
    setup.recognizer.observe.side_effect = ValueError("Bad recognition result")
    result = run(setup)
    assert result["status"] == "paused"
    assert "ValueError" in result["reason"]
    assert report(setup)["reason"] == result["reason"]
    setup.actions.perform.assert_not_called()


def test_initial_log_failure_still_closes_all_resources(setup, monkeypatch):
    monkeypatch.setattr(setup.journal, "event", Mock(side_effect=OSError("Disk error")))
    result = run(setup)
    assert result["status"] == "paused"
    assert "Disk error" in result["reason"]
    assert result["warnings"]
    setup.actions.release.assert_called_once()
    setup.capture.close.assert_called_once()
    setup.hotkey.close.assert_called_once()
    setup.actions.perform.assert_not_called()
    assert report(setup)["status"] == "paused"


def test_close_failures_do_not_skip_hotkey_or_report(setup):
    setup.recognizer.observe.side_effect = ValueError("Recognition failed")
    setup.actions.release.side_effect = RuntimeError("Release failed")
    setup.capture.close.side_effect = RuntimeError("Capture close failed")
    result = run(setup)
    setup.hotkey.close.assert_called_once()
    assert len(result["warnings"]) == 2
    assert report(setup)["warnings"] == result["warnings"]


def test_checkpoint_failure_before_input_blocks_dispatch_and_rolls_back(setup, monkeypatch):
    monkeypatch.setattr(setup.journal, "checkpoint", Mock(side_effect=OSError("No checkpoint")))
    result = run(setup)
    setup.actions.perform.assert_not_called()
    assert result["attempts"] == {}
    assert result["pending_step"] is None
    assert report(setup)["status"] == "paused"


def test_evidence_failure_does_not_consume_attempt_or_lose_report(setup, monkeypatch):
    monkeypatch.setattr(setup.journal, "frame", Mock(side_effect=ValueError("Cannot encode frame")))
    result = run(setup)
    assert result["attempts"] == {}
    setup.actions.perform.assert_not_called()
    assert report(setup)["status"] == "paused"


def test_report_write_failure_returns_in_memory_result(setup, monkeypatch):
    setup.recognizer.observe.side_effect = ValueError("Recognition failed")
    monkeypatch.setattr(setup.journal, "report", Mock(side_effect=OSError("Report write failed")))
    result = run(setup)
    assert result["status"] == "paused"
    assert "Report write failed" in result["warnings"][-1]
    setup.hotkey.close.assert_called_once()


def test_preview_does_not_create_pending_attempt(setup):
    result = run(setup, preview=True)
    assert result["status"] == "preview"
    assert result["attempts"] == {}
    assert result["pending_step"] is None
    setup.actions.perform.assert_not_called()
    assert checkpoint(setup)["pending"] is None
