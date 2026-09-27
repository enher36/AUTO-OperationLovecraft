"""Client-area capture and foreground checks; imports are safe off Windows."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import time

from .models import DesktopError, Frame, WindowInfo, WindowSpec


def _user32():
    if sys.platform != "win32":
        raise DesktopError("Live desktop automation requires Windows.")
    api = ctypes.WinDLL("user32", use_last_error=True)
    # Do this before importing either screenshot or input libraries. In an
    # embedding application the process setting can already be locked, so also
    # establish per-monitor awareness for the current capture/input thread.
    try:
        api.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        api.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        api.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        api.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        if not api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4)):
            raise DesktopError("Could not establish per-monitor DPI awareness.")
    except AttributeError as exc:
        raise DesktopError("Live automation requires Windows 10 with per-monitor DPI support.") from exc
    api.IsWindow.argtypes = [wintypes.HWND]
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.IsIconic.argtypes = [wintypes.HWND]
    api.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    api.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    api.GetForegroundWindow.restype = wintypes.HWND
    return api


def _window_info(api, handle: int) -> WindowInfo | None:
    if not api.IsWindow(handle) or not api.IsWindowVisible(handle):
        return None
    size = api.GetWindowTextLengthW(handle)
    if size <= 0:
        return None
    title = ctypes.create_unicode_buffer(size + 1)
    api.GetWindowTextW(handle, title, len(title))
    rect, origin = wintypes.RECT(), wintypes.POINT()
    if not api.GetClientRect(handle, ctypes.byref(rect)):
        return None
    if not api.ClientToScreen(handle, ctypes.byref(origin)):
        return None
    return WindowInfo(
        handle, title.value, origin.x, origin.y,
        rect.right - rect.left, rect.bottom - rect.top,
        api.GetForegroundWindow() == handle, bool(api.IsIconic(handle)),
    )


def list_windows() -> list[WindowInfo]:
    """Return visible, titled top-level windows, including minimized ones."""
    api = _user32()
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def collect(handle, _):
        info = _window_info(api, handle)
        if info is not None:
            windows.append(info)
        return True

    api.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    if not api.EnumWindows(collect, 0):
        raise DesktopError("Could not enumerate desktop windows.")
    return windows


def validate_window(info: WindowInfo, spec: WindowSpec) -> None:
    """Reject unsafe desktop state. This never activates or moves a window."""
    if spec.title_contains.casefold() not in info.title.casefold():
        raise DesktopError("The bound window title no longer matches the workflow.")
    if info.minimized:
        raise DesktopError("The game window is minimized; restore it before resuming.")
    if not info.foreground:
        raise DesktopError("The game window lost focus; automation is paused.")
    if (info.width, info.height) != (spec.width, spec.height):
        raise DesktopError(
            f"Client size is {info.width}x{info.height}; expected {spec.width}x{spec.height}."
        )


def _same_geometry(first: WindowInfo, second: WindowInfo) -> bool:
    return (first.handle, first.left, first.top, first.width, first.height) == (
        second.handle, second.left, second.top, second.width, second.height,
    )


class WindowsCapture:
    def __init__(self, spec: WindowSpec):
        self.spec = spec
        self._api = _user32()
        matches = [w for w in list_windows() if spec.title_contains.casefold() in w.title.casefold()]
        if len(matches) != 1:
            raise DesktopError(f"Expected one window matching {spec.title_contains!r}; found {len(matches)}.")
        validate_window(matches[0], spec)
        self._handle = matches[0].handle
        try:
            import mss
            self._screen = mss.mss()
        except ImportError as exc:
            raise DesktopError("Install desktop dependencies to capture the game window.") from exc

    def current_window(self) -> WindowInfo:
        info = _window_info(self._api, self._handle)
        if info is None:
            raise DesktopError("The bound game window is no longer available.")
        validate_window(info, self.spec)
        return info

    def capture(self) -> Frame:
        if self._screen is None:
            raise DesktopError("The capture session has been closed.")
        import numpy as np
        before = self.current_window()
        try:
            shot = self._screen.grab({
                "left": before.left, "top": before.top,
                "width": before.width, "height": before.height,
            })
            pixels = np.asarray(shot)[:, :, :3].copy()  # mss uses BGRA
        except Exception as exc:
            raise DesktopError(f"Could not capture the client area: {exc}") from exc
        after = self.current_window()
        if not _same_geometry(before, after):
            raise DesktopError("The window moved or resized during capture; capture again.")
        return Frame(pixels, time.monotonic(), after)

    def close(self) -> None:
        if self._screen is not None:
            self._screen.close()
            self._screen = None
