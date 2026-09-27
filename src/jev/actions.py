"""Guarded atomic desktop input and a global F8 pause signal."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
import time

from .capture import WindowsCapture, _same_geometry, _user32, validate_window
from .models import Action, DesktopError, InputNotSent, Observation


SUPPORTED_KEYS = frozenset({
    "enter", "esc", "tab", "space", "up", "down", "left", "right",
    "home", "end", "pageup", "pagedown", "backspace", "delete",
    *(f"f{i}" for i in range(1, 13) if i != 8), *"abcdefghijklmnopqrstuvwxyz0123456789",
})


def _load_pyautogui():
    _user32()  # Set DPI awareness before pyautogui can set its process defaults.
    try:
        import pyautogui
    except ImportError as exc:
        raise DesktopError("Install desktop dependencies to send mouse and keyboard input.") from exc
    pyautogui.FAILSAFE = True
    return pyautogui


class WindowsActions:
    def __init__(self, capture: WindowsCapture):
        self.capture = capture
        self._gui = None
        self._held_key = None

    def perform(self, action: Action, observation: Observation) -> None:
        if action.kind == "wait":
            return
        try:
            if action.kind not in {"click", "press"}:
                raise DesktopError(f"Unsupported action: {action.kind!r}.")
            if action.kind == "press" and action.key not in SUPPORTED_KEYS:
                raise DesktopError(f"Unsupported key: {action.key!r}.")
            if self._gui is None:
                self._gui = _load_pyautogui()
            frame = observation.frame
            if frame.window is None:
                raise DesktopError("Desktop input requires an observation bound to a game window.")
            current = self.capture.current_window()
            validate_window(current, self.capture.spec)
            age = time.monotonic() - frame.captured_at
            if not 0 <= age <= 1.0:
                raise DesktopError("The observation is stale; capture again before sending input.")
            if not _same_geometry(frame.window, current):
                raise DesktopError("The window moved, resized, or changed since recognition; capture again.")
            if action.kind == "click":
                detection = observation.detections.get(action.target)
                if detection is None:
                    raise DesktopError(f"Click target {action.target!r} was not detected.")
                x, y = detection.center
                if not (0 <= x < current.width and 0 <= y < current.height):
                    raise DesktopError("The detected click target is outside the game client area.")
        except KeyboardInterrupt as exc:
            raise InputNotSent("用户中断，操作尚未发送") from exc
        except Exception as exc:
            raise InputNotSent(str(exc)) from exc
        # From this boundary onward, a failure may have happened after input.
        try:
            if action.kind == "click":
                self._gui.click(current.left + x, current.top + y)
            else:
                # pyautogui.press contains separate key-down/up calls. Retain
                # ownership if interruption occurs before it returns.
                self._held_key = action.key
                self._gui.press(action.key)
                self._held_key = None
        except Exception as exc:
            raise DesktopError(f"Desktop input stopped: {exc}") from exc

    def release(self) -> None:
        """Release only the key potentially held by an interrupted press."""
        if self._held_key is None or self._gui is None:
            return
        failsafe = self._gui.FAILSAFE
        try:
            # Cleanup must still work when the pointer triggered the failsafe.
            self._gui.FAILSAFE = False
            self._gui.keyUp(self._held_key)
            self._held_key = None
        finally:
            self._gui.FAILSAFE = failsafe


class PauseHotkey:
    """Global F8 latch. Use ``with PauseHotkey() as pause`` or start()/close().

    is_pressed() stays true after F8 until a new start(). It sends no input and
    never takes focus. Registration failure raises DesktopError from start().
    The runner should poll the latch between capture/recognition/action calls.
    """

    def __init__(self):
        self._pressed = threading.Event()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = None
        self._error = None

    def start(self) -> "PauseHotkey":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._pressed.clear()
        self._stop.clear()
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="jev-pause-hotkey", daemon=True)
        self._thread.start()
        if not self._ready.wait(2):
            self.close()
            raise DesktopError("The F8 pause hotkey did not initialize.")
        if self._error is not None:
            self.close()
            raise DesktopError(f"Could not register F8 pause hotkey: {self._error}")
        return self

    def _run(self) -> None:
        api = None
        registered = False
        hotkey_id = 0x4A45
        try:
            api = _user32()
            api.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            api.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            api.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                         wintypes.UINT, wintypes.UINT, wintypes.UINT]
            registered = bool(api.RegisterHotKey(None, hotkey_id, 0x4000, 0x77))  # NOREPEAT, F8
            if not registered:
                raise DesktopError("F8 may already be reserved by another application.")
            self._ready.set()
            message = wintypes.MSG()
            while not self._stop.is_set():
                while api.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    if message.message == 0x0312 and message.wParam == hotkey_id:  # WM_HOTKEY
                        self._pressed.set()
                self._stop.wait(0.02)
        except Exception as exc:
            self._error = exc
            self._pressed.set()  # Fail closed if the hotkey listener unexpectedly stops.
        finally:
            if registered:
                api.UnregisterHotKey(None, hotkey_id)
            self._ready.set()

    def is_pressed(self) -> bool:
        return self._pressed.is_set()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def __enter__(self) -> "PauseHotkey":
        return self.start()

    def __exit__(self, *_):
        self.close()
