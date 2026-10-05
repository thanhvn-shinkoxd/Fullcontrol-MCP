"""Serialized desktop actions with explicit screenshot coordinate mapping."""

from __future__ import annotations

import io
import math
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone

from .native import WindowsAPI, key_event, mouse_event

KEYS = {
    "BACKSPACE": 0x08, "TAB": 0x09, "ENTER": 0x0D, "RETURN": 0x0D,
    "SHIFT": 0x10, "CTRL": 0x11, "CONTROL": 0x11, "ALT": 0x12,
    "PAUSE": 0x13, "CAPSLOCK": 0x14, "ESC": 0x1B, "ESCAPE": 0x1B,
    "SPACE": 0x20, "PAGEUP": 0x21, "PAGEDOWN": 0x22, "END": 0x23,
    "HOME": 0x24, "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "PRINTSCREEN": 0x2C, "INSERT": 0x2D, "DELETE": 0x2E, "WIN": 0x5B,
    "LWIN": 0x5B, "RWIN": 0x5C,
    **{str(i): ord(str(i)) for i in range(10)},
    **{chr(i): i for i in range(ord("A"), ord("Z") + 1)},
    **{f"F{i}": 0x6F + i for i in range(1, 25)},
}
BUTTONS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}


@dataclass(frozen=True)
class Observation:
    id: str
    created: float
    bounds: dict
    image_width: int
    image_height: int
    hwnd: int

    def to_desktop(self, x: float, y: float) -> tuple[int, int]:
        if not (0 <= x < self.image_width and 0 <= y < self.image_height):
            raise ValueError("Image coordinates fall outside this observation")
        return (
            self.bounds["left"] + min(self.bounds["width"] - 1,
                                      int(x * self.bounds["width"] / self.image_width)),
            self.bounds["top"] + min(self.bounds["height"] - 1,
                                     int(y * self.bounds["height"] / self.image_height)),
        )


def motion_points(start: tuple[int, int], end: tuple[int, int], steps: int):
    """Ease in/out on a shallow quadratic arc, with an exact final destination."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    distance = math.hypot(dx, dy)
    bend = min(60.0, distance * 0.12)
    control = ((start[0] + end[0]) / 2 - (dy / distance * bend if distance else 0),
               (start[1] + end[1]) / 2 + (dx / distance * bend if distance else 0))
    for step in range(1, steps + 1):
        progress = step / steps
        t = progress * progress * (3 - 2 * progress)
        yield (
            round((1 - t) ** 2 * start[0] + 2 * (1 - t) * t * control[0] + t * t * end[0]),
            round((1 - t) ** 2 * start[1] + 2 * (1 - t) * t * control[1] + t * t * end[1]),
        )


class Desktop:
    def __init__(self, api=None) -> None:
        self._api = api
        self._lock = threading.RLock()
        self._paused = threading.Event()
        self._observations: OrderedDict[str, Observation] = OrderedDict()
        self._automation = None

    @property
    def api(self):
        if self._api is None:
            self._api = WindowsAPI()
        return self._api

    def control(self, action: str = "status") -> dict:
        if action == "pause":
            self._paused.set()
        elif action == "resume":
            self._paused.clear()
        elif action != "status":
            raise ValueError("action must be status, pause, or resume")
        return {"paused": self._paused.is_set(), "emergency_shortcut": "CTRL+ALT+F12"}

    def _check_running(self) -> None:
        if self.api.emergency_key_down():
            self._paused.set()
        if self._paused.is_set():
            raise RuntimeError("Desktop input is paused. Use desktop_control(action='resume')")

    def status(self) -> dict:
        with self._lock:
            return {**self.api.status(), **self.control(), "keyboard_keys": sorted(KEYS)}

    def _guard(self, observation_id: str | None, expected_window: int | None) -> Observation | None:
        self._check_running()
        observation = None
        if observation_id:
            observation = self._observations.get(observation_id)
            if observation is None or time.monotonic() - observation.created > 30:
                raise ValueError("Observation is missing or older than 30 seconds; call observe again")
            if expected_window is None:
                expected_window = observation.hwnd
        if expected_window is not None and self.api.window()["hwnd"] != expected_window:
            raise RuntimeError("Foreground window changed; observe before sending more input")
        return observation

    def observe(self, monitor: int = 0, max_size: int = 1600, include_ui: bool = False) -> tuple[dict, bytes]:
        if not 256 <= max_size <= 4096:
            raise ValueError("max_size must be between 256 and 4096")
        from PIL import Image, ImageGrab

        with self._lock:
            status = self.api.status()
            if not status["interactive_session"] or not status["input_desktop_accessible"]:
                raise RuntimeError("An accessible, logged-in Windows desktop is required")
            monitors = status["monitors"]
            if not 0 <= monitor <= len(monitors):
                raise ValueError(f"monitor must be between 0 and {len(monitors)}; 0 means all screens")
            bounds = status["virtual_bounds"] if monitor == 0 else monitors[monitor - 1]
            before = self.api.window()
            started = time.monotonic()
            captured_at = datetime.now(timezone.utc).isoformat()
            try:
                image = ImageGrab.grab(
                    bbox=(bounds["left"], bounds["top"],
                          bounds["left"] + bounds["width"], bounds["top"] + bounds["height"]),
                    all_screens=True,
                    include_layered_windows=True,
                )
            except OSError as exc:
                raise RuntimeError(
                    "Screen capture failed. Check that the VM desktop is unlocked, active, and "
                    "has a working display; disconnected RDP or restricted sessions can block capture. "
                    f"Windows capture error: {exc}"
                ) from exc
            image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="PNG")
            foreground = self.api.window()
            if foreground["hwnd"] != before["hwnd"]:
                raise RuntimeError("Foreground window changed during capture; observe again")
            observation = Observation(
                uuid.uuid4().hex, started, bounds, image.width, image.height, foreground["hwnd"]
            )
            self._observations[observation.id] = observation
            while len(self._observations) > 16:
                self._observations.popitem(last=False)
            cursor_x, cursor_y = self.api.cursor()
            metadata = {
                "observation_id": observation.id, "captured_at": captured_at,
                "valid_for_seconds": max(0, round(30 - (time.monotonic() - started), 2)),
                "capture_bounds": bounds,
                "image": {"width": image.width, "height": image.height, "format": "png"},
                "coordinate_mapping": {
                    "desktop_x": "capture_bounds.left + image_x * scale_x",
                    "desktop_y": "capture_bounds.top + image_y * scale_y",
                    "scale_x": bounds["width"] / image.width,
                    "scale_y": bounds["height"] / image.height,
                },
                "cursor": {"x": cursor_x, "y": cursor_y},
                "foreground_window": foreground, "monitors": monitors,
            }
            if include_ui:
                try:
                    metadata["ui"] = self.ui_tree(foreground["hwnd"])
                except Exception as exc:
                    metadata["ui_error"] = str(exc)
            return metadata, output.getvalue()

    def _point(self, x, y, space: str, observation: Observation | None) -> tuple[int, int]:
        if x is None or y is None or not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Both x and y must be finite coordinates")
        if space == "image":
            if observation is None:
                raise ValueError("Image coordinates require observation_id from observe")
            point = observation.to_desktop(x, y)
        elif space == "desktop":
            point = (round(x), round(y))
        else:
            raise ValueError("coordinate_space must be image or desktop")
        if not any(m["left"] <= point[0] < m["left"] + m["width"] and
                   m["top"] <= point[1] < m["top"] + m["height"] for m in self.api.monitors()):
            raise ValueError("Coordinates do not fall on an attached monitor")
        return point

    def _move(self, target: tuple[int, int], duration_ms: int) -> None:
        start = self.api.cursor()
        bounds = self.api.virtual_bounds()
        steps = max(1, round(duration_ms / 1000 * 100))
        started = time.monotonic()
        for index, (x, y) in enumerate(motion_points(start, target, steps), start=1):
            self._check_running()
            nx = round((x - bounds["left"]) * 65535 / max(1, bounds["width"] - 1))
            ny = round((y - bounds["top"]) * 65535 / max(1, bounds["height"] - 1))
            self.api.send([mouse_event(0x0001 | 0x8000 | 0x4000,
                                      max(0, min(65535, nx)), max(0, min(65535, ny)))])
            remaining = started + duration_ms / 1000 * index / steps - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)

    def mouse(
        self, action: str, x: float | None = None, y: float | None = None,
        end_x: float | None = None, end_y: float | None = None,
        button: str = "left", duration_ms: int = 350, scroll_steps: int = 3,
        horizontal: bool = False, coordinate_space: str = "desktop",
        observation_id: str | None = None, expected_window: int | None = None,
    ) -> dict:
        if action not in {"move", "click", "double_click", "drag", "scroll"}:
            raise ValueError("Unsupported mouse action")
        if button not in BUTTONS or not 0 <= duration_ms <= 10000 or not -100 <= scroll_steps <= 100:
            raise ValueError("Invalid button, duration_ms (0..10000), or scroll_steps (-100..100)")
        with self._lock:
            observation = self._guard(observation_id, expected_window)
            target = None
            if x is not None or y is not None or action in {"move", "drag"}:
                target = self._point(x, y, coordinate_space, observation)
            destination = self._point(end_x, end_y, coordinate_space, observation) if action == "drag" else None
            if target is not None:
                self._move(target, duration_ms if action != "drag" else min(duration_ms, 200))
            self._guard(observation_id, expected_window)
            down, up = BUTTONS[button]
            if action in {"click", "double_click", "drag"}:
                for index in range(2 if action == "double_click" else 1):
                    self._check_running()
                    try:
                        self.api.send([mouse_event(down)])
                        if destination is not None:
                            self._move(destination, duration_ms)
                        else:
                            time.sleep(0.04)
                    finally:
                        self.api.send([mouse_event(up)])
                    if action == "double_click" and index == 0:
                        time.sleep(0.08)
            elif action == "scroll":
                self.api.send([mouse_event(0x1000 if horizontal else 0x0800, data=scroll_steps * 120)])
            cursor_x, cursor_y = self.api.cursor()
            return {"action": action, "cursor": {"x": cursor_x, "y": cursor_y},
                    "foreground_window": self.api.window()}

    def _hotkey(self, codes: list[int]) -> None:
        held = []
        try:
            for code in codes:
                self._check_running()
                held.append(code)
                self.api.send([key_event(code)])
        finally:
            for code in reversed(held):
                self.api.send([key_event(code, up=True)])

    def keyboard(
        self, action: str, text: str = "", keys: list[str] | None = None,
        interval_ms: int = 15, observation_id: str | None = None,
        expected_window: int | None = None,
    ) -> dict:
        if action not in {"type", "paste", "press"}:
            raise ValueError("action must be type, paste, or press")
        if len(text) > 10000 or not 0 <= interval_ms <= 200 or len(text) * interval_ms > 60000:
            raise ValueError("Text is limited to 10000 characters and typing to 60 seconds")
        if "\x00" in text:
            raise ValueError("Text cannot contain null characters")
        codes = []
        if action == "press":
            if not keys or len(keys) > 8:
                raise ValueError("press requires 1..8 keys, for example ['CTRL', 'S']")
            for key in keys:
                if key.upper() not in KEYS:
                    raise ValueError(f"Unknown key: {key}; use type for printable text")
                codes.append(KEYS[key.upper()])
        with self._lock:
            observation = self._guard(observation_id, expected_window)
            guard_window = expected_window
            if guard_window is None and observation is not None:
                guard_window = observation.hwnd
            if action == "press":
                self._hotkey(codes)
            elif action == "paste":
                self.api.clipboard_set(text)
                self._hotkey([KEYS["CTRL"], KEYS["V"]])
            else:
                for char in text.replace("\r\n", "\n").replace("\r", "\n"):
                    self._guard(None, guard_window)
                    if char in {"\n", "\t"}:
                        self._hotkey([KEYS["ENTER"] if char == "\n" else KEYS["TAB"]])
                    else:
                        encoded = char.encode("utf-16-le")
                        for index in range(0, len(encoded), 2):
                            unit = int.from_bytes(encoded[index:index + 2], "little")
                            try:
                                self.api.send([key_event(unit, unicode=True)])
                            finally:
                                self.api.send([key_event(unit, unicode=True, up=True)])
                    if interval_ms:
                        time.sleep(interval_ms / 1000)
            return {"action": action, "characters": len(text) if action != "press" else 0,
                    "clipboard_changed": action == "paste", "foreground_window": self.api.window()}

    def windows(self) -> list[dict]:
        with self._lock:
            return self.api.windows()

    def focus_window(self, hwnd: int) -> dict:
        with self._lock:
            self._check_running()
            return self.api.focus(hwnd)

    def clipboard(self, text: str | None = None) -> dict:
        with self._lock:
            if text is not None:
                self._check_running()
                if len(text) > 100000 or "\x00" in text:
                    raise ValueError("Clipboard text must be <= 100000 characters without nulls")
                self.api.clipboard_set(text)
                return {"characters": len(text)}
            value = self.api.clipboard_get()
            return {"text": value[:100000], "truncated": len(value) > 100000}

    def ui_tree(self, hwnd: int | None = None, max_depth: int = 3, max_nodes: int = 200) -> dict:
        if not 0 <= max_depth <= 8 or not 1 <= max_nodes <= 1000:
            raise ValueError("max_depth must be 0..8 and max_nodes must be 1..1000")
        with self._lock:
            handle = hwnd or self.api.window()["hwnd"]
            if not handle:
                raise RuntimeError("No foreground window")
            if self._automation is None:
                from .automation import UIAutomation

                self._automation = UIAutomation()
            return self._automation.tree(handle, max_depth, max_nodes)

    def close(self) -> None:
        self._paused.set()
        if self._automation:
            self._automation.close()
