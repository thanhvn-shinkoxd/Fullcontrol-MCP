"""MCP tool contracts. Desktop operations stay on a single serialized backend."""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from functools import partial
from typing import Literal

import anyio
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import TextContent, ToolAnnotations

from . import __version__
from .config import Settings
from .desktop import Desktop
from .files import FileService
from .jobs import JobManager

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)


@dataclass
class Runtime:
    mcp: FastMCP
    desktop: Desktop
    files: FileService
    jobs: JobManager

    async def close(self) -> None:
        try:
            self.desktop.close()
        finally:
            with anyio.CancelScope(shield=True):
                await self.jobs.close()


async def threaded(function, *args, **kwargs):
    # Do not abandon a desktop action when a client disconnects mid-input.
    return await anyio.to_thread.run_sync(partial(function, *args, **kwargs))


def build_runtime(settings: Settings, desktop: Desktop | None = None) -> Runtime:
    desktop = desktop or Desktop()
    files = FileService(settings.workspace, settings.max_inline_bytes, settings.max_upload_bytes)
    jobs = JobManager(settings.data_dir, settings.workspace, settings.max_jobs, settings.max_log_bytes)
    mcp = FastMCP(
        "FullRemote Windows",
        instructions=(
            "Operate the Windows VM through observe -> action -> observe. Screenshots may be scaled; "
            "use mouse coordinate_space='image' with observation_id to map image pixels correctly. "
            "An observation expires after 30 seconds and checks the foreground HWND, not all UI content. "
            "Input success means events were injected, not that the application completed its task. "
            "Do not blindly retry a failed action because some input may already have been sent. "
            "Desktop actions are serialized individually; use one controlling AI at a time. "
            "Long commands return job_id; poll job_status or use job_cancel. "
            "File paths may be absolute; relative paths resolve against the configured workspace."
        ),
        host=settings.host, port=settings.port, streamable_http_path="/mcp",
        stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.trusted_hosts, allowed_origins=settings.trusted_origins,
        ),
    )

    @mcp.tool(annotations=READ)
    async def system_info() -> dict:
        """Get server paths, Windows session/elevation, monitors, and supported keyboard keys."""
        result = {"version": __version__, "platform": platform.platform(),
                  "python": platform.python_version(), "workspace": str(settings.workspace),
                  "data_dir": str(settings.data_dir)}
        try:
            result["desktop"] = await threaded(desktop.status)
        except Exception as exc:
            result["desktop_error"] = str(exc)
        return result

    @mcp.tool(annotations=READ, structured_output=False)
    async def observe(monitor: int = 0, max_size: int = 1600, include_ui: bool = False) -> list:
        """Return screenshot and metadata. monitor=0 captures all screens; max_size=256..4096.

        Image pixels can be used by mouse with coordinate_space='image' and observation_id.
        include_ui adds a bounded UI Automation tree; unavailable UIA is reported separately.
        """
        metadata, png = await threaded(desktop.observe, monitor, max_size, include_ui)
        return [TextContent(type="text", text=json.dumps(metadata, ensure_ascii=True)),
                Image(data=png, format="png").to_image_content()]

    @mcp.tool(annotations=WRITE)
    async def mouse(
        action: Literal["move", "click", "double_click", "drag", "scroll"],
        x: float | None = None, y: float | None = None,
        end_x: float | None = None, end_y: float | None = None,
        button: Literal["left", "right", "middle"] = "left", duration_ms: int = 350,
        scroll_steps: int = 3, horizontal: bool = False,
        coordinate_space: Literal["desktop", "image"] = "desktop",
        observation_id: str | None = None, expected_window: int | None = None,
    ) -> dict:
        """Move/click/drag/scroll with smooth motion (0..10000 ms).

        Drag starts at x,y and ends at end_x,end_y. Click/scroll omit x,y to use current cursor.
        Positive scroll_steps scroll up (or right if horizontal); negative scrolls down/left.
        Desktop coordinates are physical pixels, including negative multi-monitor coordinates.
        Image coordinates require observation_id. Providing an observation also checks focus.
        """
        return await threaded(desktop.mouse, action, x, y, end_x, end_y, button, duration_ms,
                              scroll_steps, horizontal, coordinate_space, observation_id, expected_window)

    @mcp.tool(annotations=WRITE)
    async def keyboard(
        action: Literal["type", "paste", "press"], text: str = "", keys: list[str] | None = None,
        interval_ms: int = 15, observation_id: str | None = None, expected_window: int | None = None,
    ) -> dict:
        """Type Unicode, paste text, or press a chord such as keys=['CTRL','S'].

        paste replaces the clipboard and leaves it there. type sends Enter/Tab for newlines/tabs;
        paste is preferable for multiline text. Keys are always released, including on failure.
        Text <=10000 characters, interval_ms=0..200, total typing delay <=60 seconds.
        """
        return await threaded(desktop.keyboard, action, text, keys, interval_ms,
                              observation_id, expected_window)

    @mcp.tool(annotations=WRITE)
    async def desktop_control(action: Literal["status", "pause", "resume"] = "status") -> dict:
        """Pause/resume desktop input, including an action already running. Does not stop command jobs.

        Holding CTRL+ALT+F12 locally during input also pauses it. Observe remains available.
        """
        return desktop.control(action)

    @mcp.tool(annotations=READ)
    async def windows() -> list[dict]:
        """List visible top-level windows with HWND, PID, title, class, and desktop rectangles."""
        return await threaded(desktop.windows)

    @mcp.tool(annotations=WRITE)
    async def focus_window(hwnd: int) -> dict:
        """Restore/focus a window. Reports an error if Windows refuses the foreground change."""
        return await threaded(desktop.focus_window, hwnd)

    @mcp.tool(annotations=READ)
    async def ui_tree(hwnd: int | None = None, max_depth: int = 3, max_nodes: int = 200) -> dict:
        """Read UI Automation controls, names, IDs and physical rectangles for a window.

        Defaults to the foreground window. Custom-drawn controls may not expose a UIA tree.
        """
        return await threaded(desktop.ui_tree, hwnd, max_depth, max_nodes)

    @mcp.tool(annotations=WRITE)
    async def clipboard(text: str | None = None) -> dict:
        """Read text from the clipboard, or replace it when text is provided (max 100000 chars)."""
        return await threaded(desktop.clipboard, text)

    @mcp.tool(name="exec", annotations=WRITE)
    async def execute(command: str, cwd: str | None = None, timeout_seconds: float = 300,
                      wait_seconds: float = 1) -> dict:
        """Run a PowerShell script with UTF-8 output. Returns a persistent job_id and initial output.

        wait_seconds=0..10; timeout_seconds=0.1..86400 applies to the process tree.
        Use job_status to poll; job_cancel terminates the managed tree. Commands are noninteractive.
        """
        return await jobs.powershell(command, cwd, timeout_seconds, wait_seconds)

    @mcp.tool(annotations=WRITE)
    async def run_process(program: str, arguments: list[str] | None = None, cwd: str | None = None,
                          timeout_seconds: float = 300, wait_seconds: float = 1) -> dict:
        """Start an executable with an argument array, without shell interpolation.

        The managed Windows process tree stays alive until it exits, is cancelled, or times out.
        Server shutdown also terminates managed children. Increase timeout for interactive apps.
        """
        return await jobs.start([program, *(arguments or [])], cwd, timeout_seconds, wait_seconds)

    @mcp.tool(annotations=READ)
    async def job_status(job_id: str, stdout_offset: int = 0, stderr_offset: int = 0,
                         max_bytes: int = 16384) -> dict:
        """Poll a job and read bounded output. Offsets/next_offset are bytes, not characters.

        Includes recent output tails even when on-disk logs reached their configured cap.
        exit_code is the root process's code; state tracks the managed tree's lifetime.
        """
        return jobs.status(job_id, stdout_offset, stderr_offset, max_bytes)

    @mcp.tool(annotations=READ)
    async def list_jobs(limit: int = 20) -> list[dict]:
        """List recent running and persisted jobs, newest first (limit=1..100)."""
        return jobs.list_jobs(limit)

    @mcp.tool(annotations=WRITE)
    async def job_cancel(job_id: str) -> dict:
        """Terminate a job and its managed child processes, then return the final result."""
        return await jobs.cancel(job_id)

    @mcp.tool(annotations=READ)
    async def process_info(pid: int) -> dict:
        """Inspect a process for debugging: command line, working directory, memory and children."""
        def inspect():
            import psutil

            process = psutil.Process(pid)
            result = process.as_dict(attrs=["pid", "name", "status", "cmdline", "cwd", "create_time", "num_threads"])
            try:
                result["memory"] = process.memory_info()._asdict()
                result["children"] = [child.as_dict(attrs=["pid", "name", "status"])
                                      for child in process.children(recursive=True)]
            except (psutil.AccessDenied, psutil.NoSuchProcess) as exc:
                result["detail_error"] = str(exc)
            return result

        return await threaded(inspect)

    @mcp.tool(annotations=READ)
    async def list_directory(path: str = ".", offset: int = 0, limit: int = 200) -> dict:
        """List a directory with pagination. Absolute paths are allowed; this is not a file jail."""
        return await threaded(files.list_directory, path, offset, limit)

    @mcp.tool(annotations=READ)
    async def read_text(path: str, max_bytes: int = 65536) -> dict:
        """Read a bounded UTF-8 text file. Use download for binary files or byte-offset pagination."""
        return await threaded(files.read_text, path, max_bytes)

    @mcp.tool(annotations=WRITE)
    async def write_text(path: str, text: str, overwrite: bool = False) -> dict:
        """Atomically write UTF-8 text. Parent directory must exist; replacement requires overwrite."""
        return await threaded(files.write_text, path, text, overwrite)

    @mcp.tool(annotations=READ)
    async def download(path: str, offset: int = 0, max_bytes: int = 65536) -> dict:
        """Read a file chunk as base64 with byte offsets. Use transfer_info for large HTTP downloads."""
        return await threaded(files.download, path, offset, max_bytes)

    @mcp.tool(annotations=WRITE)
    async def upload(path: str, content_base64: str, overwrite: bool = False,
                     sha256: str | None = None) -> dict:
        """Atomically upload a small base64 file with optional SHA-256. Parent directory must exist.

        Default inline limit is 1 MiB; transfer_info provides streaming endpoints for larger files.
        """
        return await threaded(files.upload, path, content_base64, overwrite, sha256)

    @mcp.tool(annotations=READ)
    async def transfer_info(path: str) -> dict:
        """Get relative HTTP upload/download URLs and limits. These endpoints require HTTP transport."""
        return files.transfer_info(path)

    return Runtime(mcp, desktop, files, jobs)
