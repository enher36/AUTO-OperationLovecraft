from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from jev import actions, capture
from jev.models import Action, DesktopError, Detection, Frame, InputNotSent, Observation, WindowInfo, WindowSpec


@pytest.fixture
def desktop(monkeypatch):
    window = WindowInfo(123, "Game", 100, 200, 800, 600)
    source = SimpleNamespace(spec=WindowSpec("Game", 800, 600), current_window=Mock(return_value=window))
    gui = Mock()
    gui.FAILSAFE = True
    monkeypatch.setattr(actions, "_load_pyautogui", lambda: gui)
    monkeypatch.setattr(actions.time, "monotonic", lambda: 10.0)
    observation = Observation(Frame(None, 9.5, window), {"button": Detection("button", 1.0, (30, 40))})
    return actions.WindowsActions(source), source, gui, observation


def test_click_uses_client_coordinates(desktop):
    executor, source, gui, observation = desktop
    executor.perform(Action("click", "button"), observation)
    source.current_window.assert_called_once()
    gui.click.assert_called_once_with(130, 240)


@pytest.mark.parametrize("changes, message", [
    ({"foreground": False}, "lost focus"),
    ({"left": 101}, "moved"),
    ({"top": 201}, "moved"),
    ({"handle": 124}, "changed"),
    ({"width": 801}, "Client size"),
    ({"minimized": True}, "minimized"),
    ({"title": "Another app"}, "title"),
])
@pytest.mark.parametrize("action", [Action("click", "button"), Action("press", key="enter")])
def test_changed_desktop_prevents_all_input(desktop, changes, message, action):
    executor, source, gui, observation = desktop
    source.current_window.return_value = replace(observation.frame.window, **changes)
    with pytest.raises(InputNotSent, match=message):
        executor.perform(action, observation)
    assert not gui.mock_calls


@pytest.mark.parametrize("timestamp", [8.99, 10.1])
def test_stale_or_future_frame_prevents_input(desktop, timestamp):
    executor, _, gui, observation = desktop
    observation = replace(observation, frame=replace(observation.frame, captured_at=timestamp))
    with pytest.raises(InputNotSent, match="stale"):
        executor.perform(Action("click", "button"), observation)
    assert not gui.mock_calls


@pytest.mark.parametrize("center", [(-1, 0), (0, -1), (800, 0), (0, 600), (float("nan"), 0)])
def test_click_bounds_prevent_input(desktop, center):
    executor, _, gui, observation = desktop
    observation.detections["button"] = Detection("button", 1.0, center)
    with pytest.raises(InputNotSent, match="outside"):
        executor.perform(Action("click", "button"), observation)
    assert not gui.mock_calls


def test_missing_detection_prevents_input(desktop):
    executor, _, gui, observation = desktop
    with pytest.raises(DesktopError, match="not detected"):
        executor.perform(Action("click", "missing"), observation)
    assert not gui.mock_calls


def test_missing_window_prevents_input(desktop):
    executor, source, gui, observation = desktop
    source.current_window.side_effect = DesktopError("Window missing")
    with pytest.raises(DesktopError, match="missing"):
        executor.perform(Action("press", key="enter"), observation)
    assert not gui.mock_calls


def test_unbound_frame_prevents_input(desktop):
    executor, _, gui, observation = desktop
    observation = replace(observation, frame=replace(observation.frame, window=None))
    with pytest.raises(DesktopError, match="bound"):
        executor.perform(Action("press", key="enter"), observation)
    assert not gui.mock_calls


def test_press_and_release_do_not_release_user_keys(desktop):
    executor, _, gui, observation = desktop
    executor.perform(Action("press", key="enter"), observation)
    executor.release()
    gui.press.assert_called_once_with("enter")
    gui.keyUp.assert_not_called()


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), RuntimeError("Input interrupted")])
def test_interrupted_press_releases_only_its_key_with_failsafe_temporarily_disabled(desktop, failure):
    executor, _, gui, observation = desktop
    gui.press.side_effect = failure
    expected = KeyboardInterrupt if isinstance(failure, KeyboardInterrupt) else DesktopError
    with pytest.raises(expected):
        executor.perform(Action("press", key="enter"), observation)

    def key_up(key):
        assert key == "enter"
        assert gui.FAILSAFE is False

    gui.keyUp.side_effect = key_up
    executor.release()
    gui.keyUp.assert_called_once_with("enter")
    assert gui.FAILSAFE is True
    executor.release()
    gui.keyUp.assert_called_once_with("enter")


def test_failed_release_restores_failsafe_and_keeps_key_for_cleanup_retry(desktop):
    executor, _, gui, observation = desktop
    gui.press.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        executor.perform(Action("press", key="space"), observation)
    gui.keyUp.side_effect = RuntimeError("Release failed")
    with pytest.raises(RuntimeError, match="Release failed"):
        executor.release()
    assert gui.FAILSAFE is True
    gui.keyUp.side_effect = None
    executor.release()
    assert gui.keyUp.call_count == 2
    assert executor._held_key is None


def test_wait_sends_no_input_or_imports(desktop, monkeypatch):
    executor, source, gui, observation = desktop
    monkeypatch.setattr(actions, "_load_pyautogui", Mock(side_effect=AssertionError("No import needed")))
    executor.perform(Action("wait"), observation)
    source.current_window.assert_not_called()
    assert not gui.mock_calls


@pytest.mark.parametrize("key", ["ctrl", "alt", "win", "ENTER", "f8", "f13", "é", None])
def test_unsupported_keys_prevent_input(desktop, key):
    executor, _, gui, observation = desktop
    with pytest.raises(DesktopError, match="Unsupported key"):
        executor.perform(Action("press", key=key), observation)
    assert not gui.mock_calls


def test_input_failure_is_actionable(desktop):
    executor, _, gui, observation = desktop
    gui.click.side_effect = RuntimeError("failsafe triggered")
    with pytest.raises(DesktopError, match="failsafe") as raised:
        executor.perform(Action("click", "button"), observation)
    assert not isinstance(raised.value, InputNotSent)


def test_dependency_initialization_failure_is_known_unsent(desktop, monkeypatch):
    executor, _, gui, observation = desktop
    monkeypatch.setattr(actions, "_load_pyautogui", Mock(side_effect=ImportError("Unavailable")))
    with pytest.raises(InputNotSent, match="Unavailable"):
        executor.perform(Action("press", key="enter"), observation)
    assert not gui.mock_calls


def test_interrupt_during_dependency_initialization_is_known_unsent(desktop, monkeypatch):
    executor, _, gui, observation = desktop
    monkeypatch.setattr(actions, "_load_pyautogui", Mock(side_effect=KeyboardInterrupt()))
    with pytest.raises(InputNotSent):
        executor.perform(Action("press", key="enter"), observation)
    assert not gui.mock_calls


def test_capture_rejects_ambiguous_window_without_loading_mss(monkeypatch):
    monkeypatch.setattr(capture, "_user32", lambda: object())
    monkeypatch.setattr(capture, "list_windows", lambda: [
        WindowInfo(1, "Game", 0, 0, 800, 600), WindowInfo(2, "Game Two", 0, 0, 800, 600),
    ])
    with pytest.raises(DesktopError, match="found 2"):
        capture.WindowsCapture(WindowSpec("Game", 800, 600))


def test_capture_copies_bgr_and_rechecks_window():
    source = capture.WindowsCapture.__new__(capture.WindowsCapture)
    window = WindowInfo(1, "Game", 10, 20, 2, 1)
    source.current_window = Mock(return_value=window)
    source._screen = Mock()
    source._screen.grab.return_value = np.array([[[1, 2, 3, 255], [4, 5, 6, 255]]], dtype=np.uint8)
    frame = source.capture()
    assert frame.image.tolist() == [[[1, 2, 3], [4, 5, 6]]]
    assert frame.window == window
    assert source.current_window.call_count == 2
    source._screen.grab.assert_called_once_with({"left": 10, "top": 20, "width": 2, "height": 1})


def test_window_move_during_capture_invalidates_image():
    source = capture.WindowsCapture.__new__(capture.WindowsCapture)
    window = WindowInfo(1, "Game", 10, 20, 2, 1)
    source.current_window = Mock(side_effect=[window, replace(window, left=11)])
    source._screen = Mock()
    source._screen.grab.return_value = np.zeros((1, 2, 4), dtype=np.uint8)
    with pytest.raises(DesktopError, match="during capture"):
        source.capture()


def test_non_windows_import_and_live_error(monkeypatch):
    monkeypatch.setattr(capture.sys, "platform", "linux")
    with pytest.raises(DesktopError, match="requires Windows"):
        capture.list_windows()


def test_hotkey_registration_failure_stops_cleanly(monkeypatch):
    monkeypatch.setattr(actions, "_user32", Mock(side_effect=DesktopError("Unavailable")))
    pause = actions.PauseHotkey()
    with pytest.raises(DesktopError, match="Unavailable"):
        pause.start()
    assert pause.is_pressed()
    assert not pause._thread.is_alive()


def test_hotkey_sets_latch_and_unregisters(monkeypatch):
    api = SimpleNamespace(RegisterHotKey=Mock(return_value=True),
                          UnregisterHotKey=Mock(), PeekMessageW=Mock())

    def receive(message, *_):
        if api.PeekMessageW.call_count == 1:
            message._obj.message = 0x0312
            message._obj.wParam = 0x4A45
            return True
        return False

    api.PeekMessageW.side_effect = receive
    monkeypatch.setattr(actions, "_user32", lambda: api)
    with actions.PauseHotkey() as pause:
        assert pause._pressed.wait(1)
        assert pause.is_pressed()
        assert pause.start() is pause
    api.RegisterHotKey.assert_called_once_with(None, 0x4A45, 0x4000, 0x77)
    api.UnregisterHotKey.assert_called_once_with(None, 0x4A45)
    assert not pause._thread.is_alive()
