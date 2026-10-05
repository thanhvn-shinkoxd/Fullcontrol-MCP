"""Persistent command jobs with bounded logs and process-tree cancellation."""

from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import os
import re
import shutil
import signal
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes as wt
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD),
    ]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits), ("IoInfo", _IOCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wt.DWORD), ("TotalProcesses", wt.DWORD),
        ("ActiveProcesses", wt.DWORD), ("TotalTerminatedProcesses", wt.DWORD),
    ]


class ProcessTree:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.handle = None
        if os.name == "nt":
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            bindings = [
                ("CreateJobObjectW", [ctypes.c_void_p, wt.LPCWSTR], wt.HANDLE),
                ("SetInformationJobObject", [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD], wt.BOOL),
                ("QueryInformationJobObject", [wt.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                              wt.DWORD, ctypes.c_void_p], wt.BOOL),
                ("AssignProcessToJobObject", [wt.HANDLE, wt.HANDLE], wt.BOOL),
                ("TerminateJobObject", [wt.HANDLE, wt.UINT], wt.BOOL),
                ("OpenProcess", [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
                ("CloseHandle", [wt.HANDLE], wt.BOOL),
            ]
            for name, args, result in bindings:
                function = getattr(self.kernel, name)
                function.argtypes, function.restype = args, result
            self.handle = self.kernel.CreateJobObjectW(None, None)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                self.close()
                raise ctypes.WinError(ctypes.get_last_error())

    def attach_and_resume(self, process: subprocess.Popen) -> None:
        self.process = process
        if os.name != "nt":
            return
        process_handle = self.kernel.OpenProcess(0x0100 | 0x0001, False, process.pid)
        if not process_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, process_handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.kernel.CloseHandle(process_handle)
        import psutil

        # Assign the suspended process before it can spawn untracked descendants.
        psutil.Process(process.pid).resume()

    async def wait_empty(self) -> None:
        if os.name != "nt":
            return
        while self.handle:
            info = _Accounting()
            if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.ActiveProcesses == 0:
                return
            await asyncio.sleep(0.1)

    def terminate(self) -> None:
        if os.name == "nt" and self.handle:
            if not self.kernel.TerminateJobObject(self.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
        elif self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class Capture:
    def __init__(self, path: Path, limit: int) -> None:
        self.file = path.open("wb")
        self.limit = limit
        self.total = 0
        self.stored = 0
        self.tail = b""

    def feed(self, chunk: bytes) -> None:
        remaining = max(0, self.limit - self.stored)
        if remaining:
            part = chunk[:remaining]
            self.file.write(part)
            self.file.flush()
            self.stored += len(part)
        self.total += len(chunk)
        self.tail = (self.tail + chunk)[-16384:]

    def info(self) -> dict:
        return {"total_bytes": self.total, "stored_bytes": self.stored,
                "truncated": self.total > self.stored, "tail": self.tail.decode("utf-8", errors="replace")}


@dataclass
class ActiveJob:
    record: dict
    task: asyncio.Task | None = None
    tree: ProcessTree | None = None
    stop_reason: str | None = None
    captures: dict[str, Capture] = field(default_factory=dict)


class JobManager:
    def __init__(self, data_dir: Path, workspace: Path, max_jobs: int = 8, max_log_bytes: int = 8 * 1024**2) -> None:
        self.directory = data_dir / "jobs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.workspace = workspace
        self.max_jobs = max_jobs
        self.max_log_bytes = max_log_bytes
        self._active: dict[str, ActiveJob] = {}
        self._closing = False

    def _path(self, job_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError("Invalid job_id")
        return self.directory / job_id

    def _persist(self, record: dict) -> None:
        path = self._path(record["job_id"]) / "job.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=True, indent=2), encoding="utf-8")
        temporary.replace(path)

    async def start(self, argv: list[str], cwd: str | None = None, timeout_seconds: float = 300,
                    wait_seconds: float = 1) -> dict:
        if self._closing:
            raise RuntimeError("Server is shutting down")
        if not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
            raise ValueError("argv must contain an executable followed by string arguments")
        if not 0.1 <= timeout_seconds <= 86400 or not 0 <= wait_seconds <= 10:
            raise ValueError("timeout_seconds must be 0.1..86400 and wait_seconds must be 0..10")
        if len(self._active) >= self.max_jobs:
            raise RuntimeError(f"At most {self.max_jobs} jobs may run concurrently")
        working_directory = Path(cwd) if cwd else self.workspace
        if not working_directory.is_absolute():
            working_directory = self.workspace / working_directory
        working_directory = working_directory.resolve()
        if not working_directory.is_dir():
            raise NotADirectoryError(str(working_directory))
        job_id = uuid.uuid4().hex
        self._path(job_id).mkdir()
        record = {
            "job_id": job_id, "argv": argv, "cwd": str(working_directory),
            "state": "starting", "pid": None, "exit_code": None,
            "created_at": timestamp(), "finished_at": None,
            "timeout_seconds": timeout_seconds,
        }
        active = ActiveJob(record)
        self._persist(record)
        self._active[job_id] = active
        active.task = asyncio.create_task(self._run(active), name=f"job-{job_id}")
        if wait_seconds:
            try:
                await asyncio.wait_for(asyncio.shield(active.task), wait_seconds)
            except TimeoutError:
                pass
        return self.status(job_id)

    async def powershell(self, command: str, cwd: str | None = None, timeout_seconds: float = 300,
                         wait_seconds: float = 1) -> dict:
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if not executable:
            raise RuntimeError("PowerShell was not found on PATH")
        script = "$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false);\n" + command
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return await self.start(
            [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            cwd, timeout_seconds, wait_seconds,
        )

    @staticmethod
    def _pump(reader, capture: Capture) -> None:
        try:
            while chunk := reader.read(65536):
                capture.feed(chunk)
        finally:
            reader.close()
            capture.file.close()

    @staticmethod
    async def _wait_process(process: subprocess.Popen) -> None:
        while process.poll() is None:
            await asyncio.sleep(0.05)

    async def _run(self, active: ActiveJob) -> None:
        record = active.record
        job_id = record["job_id"]
        process = None
        pumps = []
        reader_pool = None
        try:
            if active.stop_reason:
                record["state"] = active.stop_reason
                return
            active.tree = ProcessTree()
            options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000004} if os.name == "nt" else {"start_new_session": True}
            env = dict(os.environ)
            env.pop("FULLREMOTE_TOKEN", None)
            env["PYTHONIOENCODING"] = "utf-8"
            async with asyncio.timeout(record["timeout_seconds"]):
                # Anonymous pipes work with restricted Windows tokens and any event loop policy.
                process = subprocess.Popen(
                    record["argv"], cwd=record["cwd"], env=env, bufsize=0,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, **options,
                )
                record["pid"] = process.pid
                active.tree.attach_and_resume(process)
                reader_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"job-{job_id}")
                for name, reader in (("stdout", process.stdout), ("stderr", process.stderr)):
                    capture = Capture(self._path(job_id) / f"{name}.log", self.max_log_bytes)
                    active.captures[name] = capture
                    pumps.append(asyncio.get_running_loop().run_in_executor(
                        reader_pool, self._pump, reader, capture
                    ))
                record["state"] = "running"
                self._persist(record)
                if active.stop_reason:
                    active.tree.terminate()
                await self._wait_process(process)
                await active.tree.wait_empty()
                await asyncio.gather(*pumps)
            record["state"] = active.stop_reason or ("completed" if process.returncode == 0 else "failed")
        except TimeoutError:
            record["state"] = "timed_out"
        except asyncio.CancelledError:
            record["state"] = active.stop_reason or "cancelled"
        except Exception as exc:
            record["state"] = "failed"
            record["error"] = str(exc)
        finally:
            if active.tree:
                active.tree.close()
            if process:
                if process.returncode is None:
                    try:
                        if os.name != "nt":
                            active.tree.terminate()
                        process.kill()
                    except ProcessLookupError:
                        pass
                try:
                    await asyncio.wait_for(self._wait_process(process), 5)
                except TimeoutError:
                    record["cleanup_error"] = "Process did not exit within five seconds"
                record["exit_code"] = process.returncode
            if pumps:
                try:
                    await asyncio.wait_for(asyncio.gather(*pumps, return_exceptions=True), 5)
                except TimeoutError:
                    record["cleanup_error"] = "Output pipes did not close within five seconds"
            if reader_pool:
                reader_pool.shutdown(wait=False, cancel_futures=True)
            if process:
                for stream in (process.stdout, process.stderr):
                    if stream and not stream.closed:
                        stream.close()
            for name, capture in active.captures.items():
                capture.file.close()
                record[name] = capture.info()
            record["finished_at"] = timestamp()
            self._persist(record)
            self._active.pop(job_id, None)

    def status(self, job_id: str, stdout_offset: int = 0, stderr_offset: int = 0,
               max_bytes: int = 16384) -> dict:
        if min(stdout_offset, stderr_offset) < 0 or not 1 <= max_bytes <= 65536:
            raise ValueError("Offsets must be nonnegative; max_bytes must be 1..65536")
        directory = self._path(job_id)
        active = self._active.get(job_id)
        if active:
            record = dict(active.record)
            record.update({name: capture.info() for name, capture in active.captures.items()})
        else:
            record = json.loads((directory / "job.json").read_text(encoding="utf-8"))
            if record["state"] in {"starting", "running"}:
                record["state"] = "interrupted"
                record["error"] = "Server stopped before recording the final result"
        for name, offset in (("stdout", stdout_offset), ("stderr", stderr_offset)):
            path = directory / f"{name}.log"
            content = b""
            if path.exists():
                with path.open("rb") as stream:
                    stream.seek(offset)
                    content = stream.read(max_bytes)
            record[name] = {**record.get(name, {}), "offset": offset,
                            "next_offset": offset + len(content),
                            "text": content.decode("utf-8", errors="replace")}
        return record

    def list_jobs(self, limit: int = 20) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be 1..100")
        paths = sorted(self.directory.glob("*/job.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        return [self.status(path.parent.name, max_bytes=1024) for path in paths[:limit]]

    async def cancel(self, job_id: str) -> dict:
        active = self._active.get(job_id)
        if active:
            active.stop_reason = "cancelled"
            if active.tree:
                active.tree.terminate()
            await asyncio.shield(active.task)
        return self.status(job_id)

    async def close(self) -> None:
        self._closing = True
        pending = list(self._active.values())
        for active in pending:
            active.stop_reason = "cancelled"
            if active.tree:
                active.tree.terminate()
        if pending:
            await asyncio.gather(*(active.task for active in pending), return_exceptions=True)
