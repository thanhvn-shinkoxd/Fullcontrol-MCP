import threading
import unittest
from unittest.mock import patch

from fullremote_mcp import automation


class AutomationWorkerTests(unittest.TestCase):
    def test_queries_and_com_lifetime_stay_on_one_thread(self):
        threads = []
        stopped = threading.Event()

        def initialize():
            threads.append(threading.get_ident())

        def query(*args):
            threads.append(threading.get_ident())
            return {"hwnd": args[0]}

        def uninitialize():
            threads.append(threading.get_ident())
            stopped.set()

        with patch.object(automation.importlib.util, "find_spec", return_value=True), \
             patch.object(automation, "_initialize", initialize), \
             patch.object(automation, "_tree", query), \
             patch.object(automation, "_uninitialize", uninitialize):
            worker = automation.UIAutomation()
            try:
                self.assertEqual(worker.tree(1, 3, 20), {"hwnd": 1})
                self.assertEqual(worker.tree(2, 3, 20), {"hwnd": 2})
            finally:
                worker.close()
                self.assertTrue(stopped.wait(2))
        self.assertEqual(len(set(threads)), 1)
        self.assertNotEqual(threads[0], threading.get_ident())

    def test_unresponsive_provider_does_not_queue_unbounded_requests(self):
        release = threading.Event()
        stopped = threading.Event()

        def blocked_query(*args):
            release.wait(3)
            return {}

        with patch.object(automation.importlib.util, "find_spec", return_value=True), \
             patch.object(automation, "_initialize"), \
             patch.object(automation, "_uninitialize", stopped.set), \
             patch.object(automation, "_tree", blocked_query), \
             patch.object(automation, "QUERY_TIMEOUT_SECONDS", 0.02):
            worker = automation.UIAutomation()
            try:
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    worker.tree(1, 3, 20)
                with self.assertRaisesRegex(RuntimeError, "previous"):
                    worker.tree(1, 3, 20)
                self.assertTrue(worker._worker.daemon)
                worker.close()
            finally:
                release.set()
                worker.close()
                self.assertTrue(stopped.wait(2))
