"""Small, typed Win32 bindings. Importing this module does not send input."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes as wt

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
        ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wt.DWORD), ("value", INPUTUNION)]


class WindowsAPI:
    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Desktop control requires Windows")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        bindings = [
            (self.user, "SendInput", [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int], wt.UINT),
            (self.user, "GetCursorPos", [ctypes.POINTER(wt.POINT)], wt.BOOL),
            (self.user, "GetForegroundWindow", [], wt.HWND),
            (self.user, "GetWindowTextLengthW", [wt.HWND], ctypes.c_int),
            (self.user, "GetWindowTextW", [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.user, "GetClassNameW", [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.user, "GetWindowRect", [wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
            (self.user, "GetWindowThreadProcessId", [wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD),
            (self.user, "GetSystemMetrics", [ctypes.c_int], ctypes.c_int),
            (self.user, "IsWindowVisible", [wt.HWND], wt.BOOL),
            (self.user, "IsWindow", [wt.HWND], wt.BOOL),
            (self.user, "SetForegroundWindow", [wt.HWND], wt.BOOL),
            (self.user, "ShowWindow", [wt.HWND, ctypes.c_int], wt.BOOL),
            (self.user, "IsIconic", [wt.HWND], wt.BOOL),
            (self.user, "GetAsyncKeyState", [ctypes.c_int], wt.SHORT),
            (self.user, "OpenInputDesktop", [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            (self.user, "CloseDesktop", [wt.HANDLE], wt.BOOL),
            (self.user, "OpenClipboard", [wt.HWND], wt.BOOL),
            (self.user, "CloseClipboard", [], wt.BOOL),
            (self.user, "EmptyClipboard", [], wt.BOOL),
            (self.user, "GetClipboardData", [wt.UINT], wt.HANDLE),
            (self.user, "SetClipboardData", [wt.UINT, wt.HANDLE], wt.HANDLE),
            (self.user, "IsClipboardFormatAvailable", [wt.UINT], wt.BOOL),
            (self.user, "CreateWindowExW", [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p], wt.HWND),
            (self.user, "DestroyWindow", [wt.HWND], wt.BOOL),
            (self.kernel, "GlobalAlloc", [wt.UINT, ctypes.c_size_t], wt.HANDLE),
            (self.kernel, "GlobalLock", [wt.HANDLE], ctypes.c_void_p),
            (self.kernel, "GlobalSize", [wt.HANDLE], ctypes.c_size_t),
            (self.kernel, "GlobalUnlock", [wt.HANDLE], wt.BOOL),
            (self.kernel, "GlobalFree", [wt.HANDLE], wt.HANDLE),
            (self.kernel, "GetCurrentProcessId", [], wt.DWORD),
            (self.kernel, "ProcessIdToSessionId", [wt.DWORD, ctypes.POINTER(wt.DWORD)], wt.BOOL),
            (self.shell, "IsUserAnAdmin", [], wt.BOOL),
        ]
        for library, name, args, result in bindings:
            function = getattr(library, name)
            function.argtypes = args
            function.restype = result
        self._enable_dpi_awareness()

    def _enable_dpi_awareness(self) -> None:
        try:
            function = self.user.SetProcessDpiAwarenessContext
            function.argtypes = [ctypes.c_void_p]
            function.restype = wt.BOOL
            function(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2, before any capture or UIA.
        except AttributeError:
            self.user.SetProcessDPIAware()

    @staticmethod
    def _error(message: str) -> OSError:
        return OSError(f"{message}: {ctypes.WinError(ctypes.get_last_error())}")

    def send(self, events: list[INPUT]) -> None:
        values = (INPUT * len(events))(*events)
        if self.user.SendInput(len(values), values, ctypes.sizeof(INPUT)) != len(values):
            raise self._error(
                "SendInput failed; check the interactive desktop and the target's integrity level"
            )

    def cursor(self) -> tuple[int, int]:
        point = wt.POINT()
        if not self.user.GetCursorPos(ctypes.byref(point)):
            raise self._error("Cannot read cursor position")
        return point.x, point.y

    def virtual_bounds(self) -> dict:
        return dict(zip(("left", "top", "width", "height"),
                        (self.user.GetSystemMetrics(index) for index in (76, 77, 78, 79)), strict=True))

    def monitors(self) -> list[dict]:
        output = []
        callback_type = ctypes.WINFUNCTYPE(
            wt.BOOL, wt.HANDLE, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM
        )

        def callback(handle, dc, rectangle, data):
            rect = rectangle.contents
            output.append({
                "index": len(output) + 1, "left": rect.left, "top": rect.top,
                "width": rect.right - rect.left, "height": rect.bottom - rect.top,
            })
            return True

        self.user.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.c_void_p, callback_type, wt.LPARAM]
        self.user.EnumDisplayMonitors.restype = wt.BOOL
        if not self.user.EnumDisplayMonitors(None, None, callback_type(callback), 0):
            raise self._error("Cannot enumerate monitors")
        return output

    def window(self, hwnd: int | None = None) -> dict:
        handle = hwnd or self.user.GetForegroundWindow()
        if not handle:
            return {"hwnd": 0, "title": "", "pid": None, "rect": None}
        if not self.user.IsWindow(handle):
            raise ValueError(f"Window no longer exists: {handle}")
        title = ctypes.create_unicode_buffer(self.user.GetWindowTextLengthW(handle) + 1)
        self.user.GetWindowTextW(handle, title, len(title))
        class_name = ctypes.create_unicode_buffer(256)
        self.user.GetClassNameW(handle, class_name, len(class_name))
        rect, pid = wt.RECT(), wt.DWORD()
        has_rect = self.user.GetWindowRect(handle, ctypes.byref(rect))
        self.user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        return {
            "hwnd": int(handle), "title": title.value, "class_name": class_name.value,
            "pid": pid.value,
            "rect": {"left": rect.left, "top": rect.top, "right": rect.right,
                     "bottom": rect.bottom} if has_rect else None,
        }

    def windows(self) -> list[dict]:
        output = []
        callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def callback(hwnd, data):
            if self.user.IsWindowVisible(hwnd):
                try:
                    info = self.window(hwnd)
                    if info["title"]:
                        output.append(info)
                except (ValueError, OSError):
                    pass  # Windows can disappear during enumeration.
            return True

        self.user.EnumWindows.argtypes = [callback_type, wt.LPARAM]
        self.user.EnumWindows.restype = wt.BOOL
        if not self.user.EnumWindows(callback_type(callback), 0):
            raise self._error("Cannot enumerate windows")
        return output

    def focus(self, hwnd: int) -> dict:
        self.window(hwnd)
        if self.user.IsIconic(hwnd):
            self.user.ShowWindow(hwnd, 9)
        self.user.SetForegroundWindow(hwnd)
        actual = self.window()
        if actual["hwnd"] != hwnd:
            raise RuntimeError("Windows refused the focus change; observe and select the window")
        return actual

    def status(self) -> dict:
        session = wt.DWORD()
        self.kernel.ProcessIdToSessionId(self.kernel.GetCurrentProcessId(), ctypes.byref(session))
        desktop = self.user.OpenInputDesktop(0, False, 0x0001)
        accessible = bool(desktop)
        if desktop:
            self.user.CloseDesktop(desktop)
        return {
            "platform": "windows", "session_id": session.value,
            "elevated": bool(self.shell.IsUserAnAdmin()),
            "input_desktop_accessible": accessible,
            "interactive_session": session.value != 0,
            "virtual_bounds": self.virtual_bounds(), "monitors": self.monitors(),
        }

    def emergency_key_down(self) -> bool:
        return all(self.user.GetAsyncKeyState(key) & 0x8000 for key in (0x11, 0x12, 0x7B))

    def _open_clipboard(self, owner=None) -> None:
        for _ in range(20):
            if self.user.OpenClipboard(owner):
                return
            time.sleep(0.025)
        raise self._error("Clipboard is busy")

    def clipboard_get(self) -> str:
        self._open_clipboard()
        try:
            if not self.user.IsClipboardFormatAvailable(13):
                return ""
            handle = self.user.GetClipboardData(13)
            pointer = self.kernel.GlobalLock(handle)
            if not pointer:
                raise self._error("Cannot read clipboard")
            try:
                length = min(self.kernel.GlobalSize(handle) // 2, 100001)
                return ctypes.wstring_at(pointer, length).split("\x00", 1)[0]
            finally:
                self.kernel.GlobalUnlock(handle)
        finally:
            self.user.CloseClipboard()

    def clipboard_set(self, text: str) -> None:
        raw = (text + "\x00").encode("utf-16-le")
        memory = self.kernel.GlobalAlloc(0x0002, len(raw))
        if not memory:
            raise self._error("Cannot allocate clipboard text")
        transferred = False
        owner = None
        try:
            pointer = self.kernel.GlobalLock(memory)
            if not pointer:
                raise self._error("Cannot lock clipboard memory")
            try:
                ctypes.memmove(pointer, raw, len(raw))
            finally:
                self.kernel.GlobalUnlock(memory)
            # A message-only window supplies clipboard ownership without showing a GUI.
            owner = self.user.CreateWindowExW(0, "STATIC", "FullRemote clipboard", 0,
                                             0, 0, 0, 0, wt.HWND(-3), None, None, None)
            if not owner:
                raise self._error("Cannot create clipboard owner")
            self._open_clipboard(owner)
            try:
                if not self.user.EmptyClipboard() or not self.user.SetClipboardData(13, memory):
                    raise self._error("Cannot set clipboard text")
                transferred = True
            finally:
                self.user.CloseClipboard()
        finally:
            if owner:
                self.user.DestroyWindow(owner)
            if not transferred:
                self.kernel.GlobalFree(memory)


def mouse_event(flags: int, x: int = 0, y: int = 0, data: int = 0) -> INPUT:
    event = INPUT(type=0)
    event.mi = MOUSEINPUT(x, y, data & 0xFFFFFFFF, flags, 0, 0)
    return event


def key_event(code: int, *, up: bool = False, unicode: bool = False) -> INPUT:
    event = INPUT(type=1)
    extended = code in {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C}
    flags = (2 if up else 0) | (4 if unicode else (1 if extended else 0))
    event.ki = KEYBDINPUT(0 if unicode else code, code if unicode else 0, flags, 0, 0)
    return event
