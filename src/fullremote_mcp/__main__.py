"""Command line entry point; diagnostics never move the mouse or type."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import platform
import secrets
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .config import Settings


def doctor(capture: Path | None = None) -> dict:
    result = {
        "version": __version__, "python": sys.version.split()[0], "platform": platform.platform(),
        "dependencies": {name: importlib.util.find_spec(name) is not None
                         for name in ("mcp", "uvicorn", "PIL", "psutil", "pywinauto", "pythoncom")},
    }
    try:
        from .desktop import Desktop

        desktop = Desktop()
        result["desktop"] = desktop.status()
        if capture:
            metadata, png = desktop.observe()
            capture.write_bytes(png)
            result["capture"] = {"path": str(capture.resolve()), "metadata": metadata}
    except Exception as exc:
        result["desktop_error"] = str(exc)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Operate a Windows VM through MCP.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("token", help="Print a new random bearer token")
    diagnostics = commands.add_parser("doctor", help="Inspect dependencies and the Windows session")
    diagnostics.add_argument("--capture", type=Path, help="Optionally save a screenshot to this path")
    serve = commands.add_parser("serve", help="Start the MCP server")
    serve.add_argument("--transport", choices=("http", "stdio"), default="http")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--ssl-certfile")
    serve.add_argument("--ssl-keyfile")
    args = parser.parse_args()
    if args.command == "token":
        print(secrets.token_urlsafe(48))
        return
    if args.command == "doctor":
        print(json.dumps(doctor(args.capture), indent=2, ensure_ascii=True))
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(Path.cwd() / ".env")
        settings = Settings.from_env()
        if args.host is not None:
            settings = replace(settings, host=args.host)
        if args.port is not None:
            if not 1 <= args.port <= 65535:
                raise ValueError("port must be 1..65535")
            settings = replace(settings, port=args.port)
        if args.transport == "http":
            settings.validate_http()
            if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
                raise ValueError("Provide both --ssl-certfile and --ssl-keyfile")
        from .server import build_runtime

        logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        runtime = build_runtime(settings)
        if args.transport == "stdio":
            async def run_stdio():
                try:
                    await runtime.mcp.run_stdio_async()
                finally:
                    await runtime.close()

            asyncio.run(run_stdio())
        else:
            import uvicorn

            from .http_api import create_http_app

            uvicorn.run(create_http_app(runtime, settings), host=settings.host, port=settings.port,
                        ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile,
                        access_log=False, workers=1, timeout_graceful_shutdown=15)
    except (ValueError, ImportError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
