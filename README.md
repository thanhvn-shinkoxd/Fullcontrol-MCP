# FullRemote MCP

<p align="center"><strong>Control an interactive Windows desktop through MCP.</strong></p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11 or newer">
  <img src="https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows&logoColor=white" alt="Windows">
  <img src="https://img.shields.io/badge/License-Apache%202.0-D22128?logo=apache" alt="Apache License 2.0">
</p>

## Demo

<iframe
  src="https://viddler.com/embed/player?id=19396&amp;color=default"
  width="100%"
  style="aspect-ratio: 16/9; width: 100%;"
  frameborder="0"
  allow="autoplay; fullscreen"
  allowfullscreen
  title="Showcase">
</iframe>

<p align="center">If the player is not displayed, <a href="https://viddler.com/8N4UeZ">watch the demo on Viddler</a>.</p>

## Overview

FullRemote MCP is a Python server for an interactive Windows VM. An AI client can inspect and
control the desktop, run commands, manage jobs and transfer files. No separate host-side gateway
is required.

| Capability | What it supports |
| --- | --- |
| Desktop | Screenshots, mouse, keyboard and Windows UI Automation |
| Commands | PowerShell, managed jobs, logs and process details |
| Files | Authenticated streaming uploads and downloads |
| Connections | Streamable HTTP remotely or `stdio` locally |

The remote transport is MCP Streamable HTTP at `/mcp`. Screenshots use native MCP image content.
Large files use authenticated streaming HTTP endpoints.

**Guide:** [Quick start](#quick-start-on-the-vm) ·
[Connect remotely](#connect-from-another-machine) · [Tool reference](#tools) ·
[Configuration](#configuration) · [License](#license)

## Practical handoff: the Windows VM

This project has been tested on the interactive Windows VM used for the remote session:

| Item | Value |
| --- | --- |
| OS | Windows 10 Pro, build 19045 |
| Remote address | `26.37.15.119` (private VPN address; it may change) |
| MCP endpoint | `http://26.37.15.119:8765/mcp` |
| Health check | `http://26.37.15.119:8765/health` |
| Transport | MCP Streamable HTTP |
| Display | one interactive monitor, approximately `870x869` capture bounds |
| Session | logged-in interactive session; desktop input is available |
| Elevation | the server process was running elevated during the test |
| Workspace | `C:\Users\Admin\Desktop` |

The endpoint is protected by a static bearer token. Do not put the token in source control,
screenshots, prompts shared with other people, or chat logs. Set it only in the client secret
store or the current process environment. The ready-to-copy client templates are in
`mcp-configs/`; replace `<FULLREMOTE_TOKEN>` before using them.

### Start the host server

On the Windows VM, open an elevated PowerShell in the folder containing the executable. The
single-file build does not need Python:

```powershell
cd C:\Users\Admin\Desktop\fullremoteMCP
$env:FULLREMOTE_TOKEN = (.\dist\fullremote-mcp.exe token)
$env:FULLREMOTE_HOST = '0.0.0.0'
$env:FULLREMOTE_PORT = '8765'
$env:FULLREMOTE_ALLOWED_HOSTS = '26.37.15.119:8765'
.\dist\fullremote-mcp.exe serve --transport http
```

The environment variables above last for that PowerShell window. To use a fixed token, set it
explicitly instead:

```powershell
$env:FULLREMOTE_TOKEN = '<FULLREMOTE_TOKEN>'
```

In classic `cmd.exe`, the equivalent is:

```bat
set "FULLREMOTE_TOKEN=<FULLREMOTE_TOKEN>"
set "FULLREMOTE_HOST=0.0.0.0"
set "FULLREMOTE_PORT=8765"
set "FULLREMOTE_ALLOWED_HOSTS=26.37.15.119:8765"
dist\fullremote-mcp.exe serve --transport http
```

For local-only use, omit the host/allowed-host variables. The default bind is
`127.0.0.1:8765`. Verify the running instance from another machine with:

```powershell
$headers = @{ Authorization = "Bearer $env:FULLREMOTE_TOKEN" }
Invoke-RestMethod http://26.37.15.119:8765/health -Headers $headers
```

If Windows Firewall blocks the private VPN interface, create a narrowly scoped inbound rule in
an elevated PowerShell (change the profile or remote address to match the VPN):

```powershell
New-NetFirewallRule -DisplayName 'FullRemote MCP 8765' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -Profile Private -RemoteAddress 26.37.15.119
```

Do not expose this HTTP endpoint directly to the public internet. Use a private VPN, SSH tunnel,
or an HTTPS reverse proxy with an additional access-control layer. The bearer token grants the
Windows account's desktop, file and process capabilities.

### Stop the host server and shut down the VM

Stop the MCP server cleanly with `Ctrl+C` in the server console. If it was started as a managed
job, use the returned `job_id` with `job_cancel`. To shut down the Windows VM after closing the
server:

```powershell
Stop-Computer -Force
```

The shutdown command is intentionally separate from the server stop. It should only be issued
when no one else is using the VM.

### Add this server to MCP clients

There is no safe one-click way to add a desktop-control server to every AI application. Add the
same server once in each client that supports custom MCP servers. Use the client's secret or
environment-variable feature for the token. The remote settings are:

```text
Transport: Streamable HTTP
URL:       http://26.37.15.119:8765/mcp
Header:    Authorization: Bearer <FULLREMOTE_TOKEN>
```

Templates are included for common clients:

- `mcp-configs/cursor.json` -- Cursor's project or user MCP configuration.
- `mcp-configs/claude-desktop.json` -- Claude Desktop local `stdio` configuration. It launches the
  executable on the same Windows machine, so edit the absolute path first. For a remote Claude
  connection, use its custom remote-MCP UI with the HTTP values above if enabled in that build.
- `mcp-configs/vscode-mcp.json` -- VS Code MCP configuration with a prompted secret input.
- `mcp-configs/generic-remote.json` -- the transport-neutral shape for Cline, Roo Code, Windsurf,
  Continue, LibreChat and other clients that expose a remote URL and headers.

For ChatGPT or another cloud-hosted AI, a private `26.37.15.119` URL is not reachable from the
cloud. Use a public HTTPS endpoint or a private connector/tunnel supported by that product, then
configure the same `/mcp` URL and bearer header. Do not paste the real token into a normal chat
message.

### Prompt for the AI controller

Paste the following as a system/developer instruction after adding the MCP server. It tells the
model what machine it is controlling without embedding the secret:

```text
You control one interactive Windows 10 Pro VM through the FullRemote Windows MCP server.

Machine facts:
- Private VPN address: 26.37.15.119
- MCP endpoint: http://26.37.15.119:8765/mcp
- OS: Windows 10 Pro, build 19045
- Workspace: C:\\Users\\Admin\\Desktop
- One interactive monitor; screenshots are usually about 870x869 pixels
- Python 3.12.10 and Node.js 24.21.0 are installed
- The desktop session is logged in and input is available; elevation may be enabled

Operating rules:
1. Call system_info once at the start and observe before every desktop action.
2. Use mouse coordinate_space='image' with the latest observation_id. Re-observe after clicks,
   typing, window changes, downloads, installs, or any unexpected result.
3. Use ui_tree when a control exposes a reliable name; otherwise use the screenshot coordinates.
4. Treat a successful mouse/keyboard result as input injection only, not proof that the task
   completed. Verify the resulting screen, file, window, or job output.
5. For long commands use exec or run_process, keep the job_id, and poll job_status.
6. Never reveal, print, save, or send the FULLREMOTE_TOKEN. Never put secrets in source files,
   screenshots, prompts, or chat responses.
7. Do not shut down, restart, delete files, install software, or send external messages unless
   the user explicitly requests that exact action.
8. One AI controls the desktop at a time. Do not blindly retry an action after a timeout; observe
   first because some input may already have been delivered.
9. If the foreground window is not the intended application, use windows/focus_window and verify.
10. Report the concrete result and any verification evidence when the task is complete.
```

## Quick start on the VM

Use Windows 10/11 or Windows Server with Desktop Experience and Python 3.11+. Python 3.12 or
3.13 is recommended. Sign in to the desktop before starting the server.

From PowerShell in this project directory:

```powershell
.\scripts\setup.ps1 -Dev
.\scripts\start.ps1
```

Setup creates `.venv`, installs the project, and creates `.env` with a random bearer token if
the file does not already exist. It preserves an existing `.env`. To select a Python executable:

```powershell
.\scripts\setup.ps1 -Python 'C:\Python312\python.exe' -Dev
```

An equivalent installation using uv:

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e '.[dev]'
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m fullremote_mcp token
```

For the uv route, put the generated token in `FULLREMOTE_TOKEN` in `.env` before starting.
Package installation needs network access or a complete local wheel cache.

The default address is `http://127.0.0.1:8765/mcp`. The server loads `.env` from its working
directory; existing process environment variables take precedence.

## Single-file Windows executable

Build on Windows using the project virtual environment:

```powershell
.\scripts\setup.ps1
.\scripts\build.ps1
```

PyInstaller produces `dist\fullremote-mcp.exe`, including Python and the runtime dependencies.
Copy just this executable to another Windows x64 machine; Python is not required there.
The executable supports the same commands and loads an optional `.env` from the working directory:

```powershell
.\dist\fullremote-mcp.exe doctor
$env:FULLREMOTE_TOKEN = & .\dist\fullremote-mcp.exe token
.\dist\fullremote-mcp.exe serve --transport http
```

For a local MCP client, set the command to the executable's absolute path and the arguments to
`["serve", "--transport", "stdio"]`. Keep the console build: stdio transport needs stdin/stdout.
Build with another Python using `scripts\build.ps1 -Python C:\Python312\python.exe`;
use `-SkipInstall` if `.[build]` is already installed in that environment.

## Connect from another machine

Connect through a private VPN/SSH tunnel, or serve HTTPS with a certificate. The default
loopback binding works with a tunnel. To listen directly on a VM's network interface, edit `.env`:

```dotenv
FULLREMOTE_HOST=0.0.0.0
FULLREMOTE_ALLOWED_HOSTS=windows-vm:8765,192.168.1.50:8765
```

Use your actual hostname/IP and port. Requests with another Host header are rejected. If an
HTTP client sends an Origin header, add its exact origin to `FULLREMOTE_ALLOWED_ORIGINS`.

For TLS terminated by this server:

```powershell
.\scripts\start.ps1 -Certificate C:\certs\vm.crt -PrivateKey C:\certs\vm.key
```

Configure an MCP client with:

| Setting | Value |
| --- | --- |
| Transport | Streamable HTTP |
| URL | `http://windows-vm:8765/mcp`, or the HTTPS/tunnel equivalent |
| Header | `Authorization: Bearer <FULLREMOTE_TOKEN from the VM>` |
| Image support | Required for the model to see screenshots |

This server uses a static bearer token. It does not implement OAuth discovery or interactive
login; the client must support supplying the Authorization header. Every HTTP route, including
`/health` and file endpoints, requires authentication. The token grants the capabilities of the
Windows account running the server. File paths are not confined to the configured workspace.

The sample client lists tools and optionally saves a screenshot without sending desktop input:

```powershell
$env:FULLREMOTE_TOKEN = '<token from the VM>'
.\.venv\Scripts\python.exe examples\client.py --url http://windows-vm:8765/mcp --capture vm.png
```

Local clients can instead launch `.venv\Scripts\python.exe` with arguments
`-m fullremote_mcp serve --transport stdio`. Set the process working directory to this project.
Stdio does not need a bearer token and does not expose the streaming file HTTP endpoints.

## Tools

| Tools | Behavior |
| --- | --- |
| `system_info` | Python/Windows version, session, elevation, displays, paths and supported keys |
| `observe` | PNG screenshot, foreground window, cursor, monitor geometry and observation ID |
| `mouse` | Smooth move, click, double-click, drag, vertical/horizontal scroll |
| `keyboard` | Unicode typing, clipboard paste or key chords such as `['CTRL', 'S']` |
| `desktop_control` | Pause/resume desktop input; observation remains available |
| `windows`, `focus_window` | Enumerate windows and request a verified foreground change |
| `ui_tree` | Bounded Windows UI Automation tree with control names, IDs and rectangles |
| `clipboard` | Read or replace Unicode clipboard text |
| `exec` | Run a noninteractive PowerShell script with UTF-8 output |
| `run_process` | Run an executable and argument array without shell interpolation |
| `job_status`, `list_jobs`, `job_cancel` | Poll output, inspect history, terminate a managed process tree |
| `process_info` | Inspect PID, command line, working directory, memory and children |
| `list_directory`, `read_text`, `write_text` | Directory listing and bounded UTF-8 file editing |
| `upload`, `download` | Small base64 transfers with optional SHA-256 and byte-offset reads |
| `transfer_info` | Streaming HTTP upload/download URLs for a path |

### Desktop observation and input

Use `observe -> action -> observe`. For example:

```json
{"name":"observe","arguments":{"monitor":0,"max_size":1600}}
```

Then click using coordinates from that screenshot:

```json
{
  "name": "mouse",
  "arguments": {
    "action": "click",
    "x": 420,
    "y": 280,
    "coordinate_space": "image",
    "observation_id": "<ID returned by observe>",
    "duration_ms": 400
  }
}
```

`monitor=0` captures the virtual desktop; positive indices select a monitor. `max_size` limits
the longest image dimension. Image coordinates require an observation ID and are mapped back
to physical desktop pixels, including negative monitor coordinates. `coordinate_space=desktop`
uses physical pixels directly. UI Automation rectangles also use desktop coordinates.

Observation IDs expire after 30 seconds and verify the foreground HWND. This cannot detect
every layout or application-state change. A successful input call only confirms input injection;
inspect the resulting screen or files to confirm the application's result. A failed call can
have sent partial input, so observe before retrying.

Desktop operations are serialized, including when an HTTP request is cancelled. Use one AI
controller per desktop; the lock serializes individual tool calls, not entire multi-call tasks.
Natural mouse motion runs locally at approximately 100 steps/second with an eased curved path.

`keyboard(action='type')` supports UTF-16 Unicode input, including surrogate pairs. Newlines
and tabs send Enter/Tab. For multiline source code, `paste` or `write_text` is usually preferable.
Paste intentionally leaves the supplied text on the clipboard. Type input is limited to 10,000
characters and 60 seconds of configured delay. Applications with raw/custom input handling may
need different keys or paste behavior.

Call `desktop_control(action='pause')` to interrupt input. Holding **Ctrl+Alt+F12 locally during
an input action** also pauses it. The shortcut is polled while sending input; it is not a global
background hotkey. Pressed keys and mouse buttons are released during cleanup. Resume explicitly
with `desktop_control(action='resume')`. Command jobs have a separate `job_cancel` control.

### Command jobs and debugging

`exec` uses PowerShell 7 when available, otherwise Windows PowerShell. Scripts are passed as
an encoded argument, with stdout/stderr configured for UTF-8. `run_process` is useful for Python,
build tools, debuggers and GUI programs that accept command-line arguments.

Both return a job ID. `wait_seconds` (0..10) controls how long the initial call waits;
`timeout_seconds` (0.1..86400, default 300) limits the job's lifetime. Increase the timeout for
interactive applications. Poll `job_status` until `state` becomes `completed`, `failed`,
`cancelled` or `timed_out`. The root process's exit code is reported separately.

On Windows, a process is created suspended, assigned to a Job Object, and then resumed. Its
descendants remain tracked even if the root exits. Timeout, cancellation, and server exit
terminate the managed tree. This does not include processes started by external services or
an already-running application through IPC. A server cannot resume a running job after restart;
unfinished saved records are reported as `interrupted`.

Jobs use anonymous pipes and dedicated output-reader threads. They work with Windows event loop
policies that cannot launch asyncio subprocesses. Each stdout/stderr log is capped at 8 MiB by
default, while a recent 16 KiB tail remains available even after the cap. `job_status` offsets are
byte offsets; a chunk may split a UTF-8 character. Job history and logs are retained under
`.fullremote/jobs`; remove old, completed job directories when no longer needed.

For debugging, combine command output, `process_info`, screenshots, and file access. For example,
run tests/builds or query `Get-WinEvent` through `exec`, then inspect the output by job ID.
Interactive debugger adapters with breakpoint/stack/variable tools and automatic crash-dump
capture are not part of this initial version.

### File transfer

Relative paths resolve against `FULLREMOTE_WORKSPACE` (default: the server working directory).
Absolute paths are supported wherever the Windows account has access. Parent directories must
already exist; create them using `exec` when needed. Writes are atomic and require `overwrite=true`
to replace an existing file. SHA-256 mismatch or a disconnected upload leaves the original intact.

Small files can go through MCP `upload`/`download` (default inline limit: 1 MiB). For larger files,
use the URL returned by `transfer_info`, on the same origin as the MCP server:

```powershell
curl.exe --fail-with-body --upload-file .\data.zip `
  --header "Authorization: Bearer $env:FULLREMOTE_TOKEN" `
  'http://127.0.0.1:8765/files/upload?path=C%3A%2Fwork%2Fdata.zip'

curl.exe --fail-with-body --get 'http://127.0.0.1:8765/files/download' `
  --data-urlencode 'path=C:/work/result.zip' `
  --header "Authorization: Bearer $env:FULLREMOTE_TOKEN" `
  --output .\result.zip
```

Upload supports the optional `X-Content-SHA256` header. Add `&overwrite=true` to explicitly replace
a file. The default streaming upload limit is 1 GiB, enforced for Content-Length and chunked
bodies. New/replaced files inherit the destination directory's ACL on Windows. Downloads support
HTTP Range requests through Starlette's FileResponse.

## Windows session and permissions

Run the agent in the logged-in user's desktop session. For automatic startup, use Task Scheduler
with **Run only when user is logged on**, an at-logon trigger, and the project as the working
directory. Do not install the desktop agent as a Session 0 Windows service.

Elevation is inherited from the process that launches the server. Launch it elevated when the
workflow requires administrator commands or input to elevated applications. It does not bypass
UAC secure desktop, logon/lock screens, or Windows input integrity restrictions. Controlling boot
and logon screens requires a separate hypervisor-console integration.

Keep the VM desktop session active. Locking it or disconnecting RDP can interrupt capture/input,
depending on Windows and the VM display driver. A VM console session is preferable for unattended
desktop workflows. UIA support depends on the application; custom-drawn controls may only be
usable through screenshots. Screenshot and input do not provide a continuous video stream.

Run one server process per desktop. Multiple Uvicorn workers or servers would have separate locks,
observations and job state. The CLI starts exactly one worker.

## Configuration

See `.env.example` for all settings. The main limits are:

| Variable | Default |
| --- | --- |
| `FULLREMOTE_TOKEN` | Required for HTTP; generate with `fullremote-mcp token` |
| `FULLREMOTE_HOST`, `FULLREMOTE_PORT` | `127.0.0.1`, `8765` |
| `FULLREMOTE_ALLOWED_HOSTS` | Additional comma-separated host:port entries |
| `FULLREMOTE_ALLOWED_ORIGINS` | Additional comma-separated origins |
| `FULLREMOTE_WORKSPACE` | Current working directory |
| `FULLREMOTE_DATA_DIR` | `WORKSPACE/.fullremote` |
| `FULLREMOTE_MAX_JOBS` | `8` |
| `FULLREMOTE_MAX_INLINE_BYTES` | `1048576` |
| `FULLREMOTE_MAX_UPLOAD_BYTES` | `1073741824` |
| `FULLREMOTE_MAX_LOG_BYTES` | `8388608` per stream, per job |

## Validation

```powershell
.\.venv\Scripts\python.exe -m fullremote_mcp doctor
.\.venv\Scripts\python.exe -m fullremote_mcp doctor --capture desktop.png
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The automated suite tests coordinates, Unicode, input cleanup/serialization, token/Host/Origin
checks, atomic transfers, output limits, persistent jobs and Windows process-tree termination.
MCP protocol tests verify initialization, discovery, image content and jobs surviving between
HTTP requests. They explicitly skip if the MCP SDK is missing; such a run is not a complete
end-to-end validation. Desktop input tests use a fake Win32 backend and do not click/type into
the current desktop. Manually verify real input and UI Automation inside the target VM.

## Layout

```text
src/fullremote_mcp/
  __main__.py    CLI, diagnostics and startup
  config.py      Environment settings
  server.py      MCP tool definitions
  http_api.py    Authentication and streaming file endpoints
  desktop.py     Serialized desktop actions and screenshot mappings
  automation.py  Dedicated UI Automation COM worker
  native.py      Win32 input/window/clipboard bindings
  files.py       Atomic file operations
  jobs.py        Process supervision and persistent output
scripts/        Windows setup/start helpers
examples/       MCP client example
tests/          Unit, HTTP, process and MCP protocol tests
```

## License

Licensed under the [Apache License 2.0](LICENSE). Original creator attribution and
translations: [NOTICE](NOTICE).
