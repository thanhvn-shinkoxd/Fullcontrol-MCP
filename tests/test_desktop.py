import ctypes
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from fullremote_mcp.desktop import Desktop, Observation, motion_points
from fullremote_mcp.native import INPUT


class FakeAPI:
    def __init__(self):
        self.events = []
        self.hwnd = 42
        self.fail_code = None
        self.emergency = False
        self.text = ""

    def emergency_key_down(self):
        return self.emergency

    def window(self):
        return {"hwnd": self.hwnd}

    def cursor(self):
        return 100, 100

    def virtual_bounds(self):
        return {"left": -1920, "top": 0, "width": 3840, "height": 1080}

    def monitors(self):
        return [{"left": -1920, "top": 0, "width": 1920, "height": 1080},
                {"left": 0, "top": 0, "width": 1920, "height": 1080}]

    def send(self, events):
        for event in events:
            self.events.append(event)
            if event.type == 1 and event.ki.wVk == self.fail_code and not event.ki.dwFlags & 2:
                raise OSError("simulated input failure")

    def clipboard_set(self, text):
        self.text = text


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.desktop = Desktop(self.api)

    def observation(self, age=0):
        frame = Observation("frame", time.monotonic() - age,
                            {"left": -1920, "top": 0, "width": 1920, "height": 1080},
                            960, 540, 42)
        self.desktop._observations[frame.id] = frame
        return frame

    def test_scaled_image_coordinates_include_negative_monitor_offset(self):
        frame = self.observation()
        self.assertEqual(frame.to_desktop(480, 270), (-960, 540))
        self.desktop.mouse("move", 480, 270, coordinate_space="image", observation_id="frame",
                           duration_ms=0)
        last = self.api.events[-1].mi
        self.assertEqual(last.dx, round(960 * 65535 / 3839))
        self.assertEqual(last.dy, round(540 * 65535 / 1079))

    def test_stale_observation_and_focus_changes_send_no_input(self):
        self.observation(age=31)
        with self.assertRaisesRegex(ValueError, "older"):
            self.desktop.mouse("click", observation_id="frame")
        self.observation()
        self.api.hwnd = 99
        with self.assertRaisesRegex(RuntimeError, "Foreground"):
            self.desktop.keyboard("type", "hello", observation_id="frame")
        self.assertEqual(self.api.events, [])

    def test_invalid_coordinates_send_no_input(self):
        for x, y in ((9999, 9999), (float("nan"), 1), (0, None)):
            with self.assertRaises(ValueError):
                self.desktop.mouse("click", x, y)
        with self.assertRaises(ValueError):
            self.desktop.mouse("move", 1, 1, coordinate_space="image")
        self.assertEqual(self.api.events, [])

    def test_focus_change_during_motion_prevents_click(self):
        self.observation()

        def move(target, duration):
            self.api.hwnd = 99

        self.desktop._move = move
        with self.assertRaisesRegex(RuntimeError, "Foreground"):
            self.desktop.mouse("click", 100, 100, observation_id="frame")
        self.assertEqual(self.api.events, [])

    def test_hotkey_failure_releases_all_pressed_keys(self):
        self.api.fail_code = ord("S")
        with self.assertRaises(OSError):
            self.desktop.keyboard("press", keys=["CTRL", "S"])
        ups = [event.ki.wVk for event in self.api.events if event.ki.dwFlags & 2]
        self.assertEqual(ups, [ord("S"), 0x11])

    def test_typing_stops_if_foreground_changes_mid_text(self):
        self.observation()
        original_send = self.api.send

        def send(events):
            original_send(events)
            self.api.hwnd = 99

        self.api.send = send
        with self.assertRaisesRegex(RuntimeError, "Foreground"):
            self.desktop.keyboard("type", "ab", observation_id="frame", interval_ms=0)
        self.assertEqual(len(self.api.events), 2)
        self.assertTrue(self.api.events[-1].ki.dwFlags & 2)

    def test_drag_failure_releases_button(self):
        original_move = self.desktop._move
        moves = 0

        def move(target, duration):
            nonlocal moves
            moves += 1
            if moves == 2:
                raise RuntimeError("drag interrupted")
            original_move(target, 0)

        self.desktop._move = move
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.desktop.mouse("drag", 100, 100, end_x=200, end_y=200, duration_ms=0)
        self.assertEqual(self.api.events[-1].mi.dwFlags, 0x0004)

    def test_unicode_and_surrogate_pairs(self):
        self.desktop.keyboard("type", "A\u1ebf\U0001f338", interval_ms=0)
        downs = [event.ki.wScan for event in self.api.events if event.ki.dwFlags == 4]
        self.assertEqual(downs, [0x41, 0x1EBF, 0xD83C, 0xDF38])
        self.assertEqual(len(self.api.events), 8)

    def test_pause_and_emergency_stop(self):
        self.desktop.control("pause")
        with self.assertRaisesRegex(RuntimeError, "paused"):
            self.desktop.mouse("click")
        self.desktop.control("resume")
        self.api.emergency = True
        with self.assertRaisesRegex(RuntimeError, "paused"):
            self.desktop.keyboard("type", "a")
        self.assertTrue(self.desktop.control()["paused"])
        self.assertEqual(self.api.events, [])

    def test_keyboard_actions_do_not_interleave(self):
        barrier = threading.Barrier(2)

        def type_text(char):
            barrier.wait()
            self.desktop.keyboard("type", char * 8, interval_ms=1)

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(type_text, ["a", "b"]))
        downs = "".join(chr(event.ki.wScan) for event in self.api.events if event.ki.dwFlags == 4)
        self.assertIn(downs, ["a" * 8 + "b" * 8, "b" * 8 + "a" * 8])

    def test_motion_ends_exactly_at_destination(self):
        self.assertEqual(list(motion_points((1, 2), (-900, 555), 23))[-1], (-900, 555))
        self.assertEqual(list(motion_points((1, 2), (1, 2), 1)), [(1, 2)])

    @unittest.skipUnless(os.name == "nt", "Win32 ABI")
    def test_sendinput_structure_has_native_size(self):
        self.assertEqual(ctypes.sizeof(INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
