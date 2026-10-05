"""Keep pywinauto's cached COM interfaces on one persistent MTA thread."""

import importlib.util
import sys
from collections import deque
from concurrent.futures import Future
from queue import Queue
from threading import Thread

QUERY_TIMEOUT_SECONDS = 15


def _initialize():
    if "pythoncom" not in sys.modules:
        sys.coinit_flags = 0
    import pythoncom

    pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)


def _uninitialize():
    import pythoncom

    pythoncom.CoUninitialize()


def _tree(hwnd: int, max_depth: int, max_nodes: int) -> dict:
    from pywinauto import Desktop

    root = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
    queue = deque([(root, 0, None)])
    nodes = []
    while queue and len(nodes) < max_nodes:
        wrapper, depth, parent = queue.popleft()
        index = len(nodes)
        node = {"id": index, "parent": parent, "depth": depth}
        nodes.append(node)
        try:
            info = wrapper.element_info
            rect = info.rectangle
            node.update({
                "name": info.name[:512], "automation_id": info.automation_id,
                "control_type": info.control_type,
                "rect": {"left": rect.left, "top": rect.top,
                         "right": rect.right, "bottom": rect.bottom},
                "enabled": info.enabled,
            })
            if depth < max_depth:
                queue.extend((child, depth + 1, index) for child in wrapper.children())
        except Exception as exc:
            node["error"] = str(exc)
    return {"hwnd": hwnd, "nodes": nodes, "truncated": bool(queue)}


class UIAutomation:
    def __init__(self):
        if not all(importlib.util.find_spec(name) for name in ("pythoncom", "pywinauto")):
            raise RuntimeError("UI Automation requires the Windows dependencies pywin32 and pywinauto")
        self._queue = Queue()
        self._pending = None
        self._closed = False
        # A hung application provider must not prevent the agent process from shutting down.
        self._worker = Thread(target=self._run, name="uia", daemon=True)
        self._worker.start()

    def _run(self):
        initialization_error = None
        try:
            _initialize()
        except Exception as exc:
            initialization_error = exc
        try:
            while (request := self._queue.get()) is not None:
                future, arguments = request
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    if initialization_error:
                        raise initialization_error
                    future.set_result(_tree(*arguments))
                except Exception as exc:
                    future.set_exception(exc)
        finally:
            if initialization_error is None:
                _uninitialize()

    def tree(self, hwnd: int, max_depth: int, max_nodes: int) -> dict:
        if self._closed:
            raise RuntimeError("UI Automation worker is closed")
        if self._pending is not None and not self._pending.done():
            raise RuntimeError("The previous UI Automation query is still waiting on an application")
        self._pending = Future()
        self._queue.put((self._pending, (hwnd, max_depth, max_nodes)))
        try:
            return self._pending.result(timeout=QUERY_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise RuntimeError("UI Automation query timed out; use screenshot observation") from exc

    def close(self):
        if not self._closed:
            self._closed = True
            self._queue.put(None)
