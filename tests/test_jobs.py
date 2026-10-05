import asyncio
import os
import sys
import unittest
from pathlib import Path

import psutil
from support import TestDirectory

from fullremote_mcp.jobs import JobManager


class JobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TestDirectory()
        self.root = Path(self.temp.name)
        self.jobs = JobManager(self.root, self.root, max_jobs=2, max_log_bytes=2048)

    async def asyncTearDown(self):
        await self.jobs.close()
        self.temp.cleanup()

    async def test_stdout_stderr_exit_code_and_persisted_status(self):
        result = await self.jobs.start([sys.executable, "-c",
            "import sys; print('hello'); print('problem', file=sys.stderr); sys.exit(7)"], wait_seconds=10)
        self.assertEqual(result["state"], "failed", result)
        self.assertEqual(result["exit_code"], 7)
        self.assertIn("hello", result["stdout"]["text"])
        self.assertIn("problem", result["stderr"]["text"])
        reloaded = JobManager(self.root, self.root)
        self.assertEqual(reloaded.status(result["job_id"])["exit_code"], 7)

    async def test_long_output_is_drained_but_disk_usage_is_capped(self):
        result = await self.jobs.start([sys.executable, "-c",
            "import sys; sys.stdout.write('x'*150000+'THE_END')"], wait_seconds=10)
        self.assertEqual(result["state"], "completed", result)
        self.assertEqual(result["stdout"]["stored_bytes"], 2048)
        self.assertTrue(result["stdout"]["truncated"])
        self.assertTrue(result["stdout"]["tail"].endswith("THE_END"))

    async def test_timeout_terminates_descendants(self):
        script = (
            "import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print(p.pid,flush=True); time.sleep(60)"
        )
        result = await self.jobs.start([sys.executable, "-c", script], timeout_seconds=1.5,
                                       wait_seconds=10)
        self.assertEqual(result["state"], "timed_out", result)
        child_pid = int(result["stdout"]["text"].strip())
        self.assertFalse(psutil.pid_exists(child_pid))
        self.assertFalse(psutil.pid_exists(result["pid"]))

    @unittest.skipUnless(os.name == "nt", "Windows Job Objects")
    async def test_child_survives_root_exit_until_cancelled(self):
        script = (
            "import subprocess,sys; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid,flush=True)"
        )
        result = await self.jobs.start([sys.executable, "-c", script], wait_seconds=0.5)
        async with asyncio.timeout(10):
            while not result["stdout"]["text"].strip():
                self.assertIn(result["state"], {"starting", "running"}, result)
                await asyncio.sleep(0.05)
                result = self.jobs.status(result["job_id"])
        self.assertEqual(result["state"], "running", result)
        child_pid = int(result["stdout"]["text"].strip())
        self.assertTrue(psutil.pid_exists(child_pid))
        result = await self.jobs.cancel(result["job_id"])
        self.assertEqual(result["state"], "cancelled", result)
        self.assertFalse(psutil.pid_exists(child_pid))

    async def test_invalid_executable_has_persisted_failure(self):
        result = await self.jobs.start([str(self.root / "missing-program.exe")], wait_seconds=10)
        self.assertEqual(result["state"], "failed")
        self.assertIn("error", result)

    async def test_cancel_before_launch_does_not_spawn_process(self):
        result = await self.jobs.start([sys.executable, "-c", "raise Exception('must not run')"],
                                       wait_seconds=0)
        result = await self.jobs.cancel(result["job_id"])
        self.assertEqual(result["state"], "cancelled")
        self.assertIsNone(result["pid"])

    async def test_concurrency_limit_and_shutdown_cleanup(self):
        first = await self.jobs.start([sys.executable, "-c", "import time; time.sleep(60)"], wait_seconds=0)
        await self.jobs.start([sys.executable, "-c", "import time; time.sleep(60)"], wait_seconds=0)
        with self.assertRaisesRegex(RuntimeError, "concurrently"):
            await self.jobs.start([sys.executable, "-c", "print('extra')"])
        await self.jobs.close()
        self.assertEqual(self.jobs.status(first["job_id"])["state"], "cancelled")

    @unittest.skipUnless(os.name == "nt", "PowerShell on Windows")
    async def test_powershell_script_preserves_quotes_and_unicode(self):
        result = await self.jobs.powershell("Write-Output ('hello ' + [char]0x1EBF)", wait_seconds=10)
        self.assertEqual(result["state"], "completed", result)
        self.assertIn("hello \u1ebf", result["stdout"]["text"])
