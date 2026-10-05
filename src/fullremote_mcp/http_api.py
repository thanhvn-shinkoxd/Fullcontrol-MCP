"""ASGI authentication and streaming file endpoints, shared with the MCP endpoint."""

from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from fnmatch import fnmatchcase

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route

from . import __version__
from .config import Settings
from .files import FileService, UploadTooLarge


def _allowed(value: str, patterns: list[str]) -> bool:
    return any(fnmatchcase(value, pattern) or
               (pattern.endswith(":*") and value == pattern[:-2]) for pattern in patterns)


class BearerAuth:
    def __init__(self, app, settings: Settings) -> None:
        settings.validate_http()
        self.app = app
        self.token = settings.token.encode("ascii")
        self.hosts = settings.trusted_hosts
        self.origins = settings.trusted_origins

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", [])
        authorization = [value for key, value in headers if key.lower() == b"authorization"]
        scheme, _, supplied = authorization[0].partition(b" ") if len(authorization) == 1 else (b"", b"", b"")
        if scheme.lower() != b"bearer" or not hmac.compare_digest(supplied, self.token):
            response = JSONResponse({"error": "Valid bearer token required"}, status_code=401,
                                    headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})
            await response(scope, receive, send)
            return
        hosts = [value.decode("latin-1").lower() for key, value in headers if key.lower() == b"host"]
        origins = [value.decode("latin-1") for key, value in headers if key.lower() == b"origin"]
        if len(hosts) != 1 or not _allowed(hosts[0], self.hosts):
            await JSONResponse({"error": "Host is not allowed"}, status_code=421)(scope, receive, send)
            return
        if origins and (len(origins) != 1 or not _allowed(origins[0], self.origins)):
            await JSONResponse({"error": "Origin is not allowed"}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def file_routes(files: FileService) -> list[Route]:
    async def health(request: Request):
        return JSONResponse({"name": "fullremote-mcp", "version": __version__, "status": "ok"})

    def failure(exc: Exception) -> JSONResponse:
        if isinstance(exc, UploadTooLarge):
            code = 413
        elif isinstance(exc, FileExistsError):
            code = 409
        elif isinstance(exc, FileNotFoundError):
            code = 404
        elif isinstance(exc, PermissionError):
            code = 403
        else:
            code = 400
        return JSONResponse({"error": str(exc)}, status_code=code)

    async def upload(request: Request):
        sink = None
        try:
            path = request.query_params.get("path")
            if not path:
                raise ValueError("path query parameter is required")
            overwrite = request.query_params.get("overwrite", "false").lower()
            if overwrite not in {"true", "false"}:
                raise ValueError("overwrite must be true or false")
            content_length = request.headers.get("content-length")
            if content_length is not None:
                size = int(content_length)
                if size < 0:
                    raise ValueError("Invalid Content-Length")
                if size > files.max_upload_bytes:
                    raise UploadTooLarge(f"Upload exceeds {files.max_upload_bytes} bytes")
            sink = await anyio.to_thread.run_sync(
                files.begin_upload, path, overwrite == "true", request.headers.get("x-content-sha256")
            )
            async for chunk in request.stream():
                await anyio.to_thread.run_sync(sink.write, chunk)
            if content_length is not None and sink.size != int(content_length):
                raise ValueError("Received bytes do not match Content-Length")
            result = await anyio.to_thread.run_sync(sink.finish)
            return JSONResponse(result, status_code=201)
        except (OSError, ValueError) as exc:
            return failure(exc)
        finally:
            if sink:
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(sink.abort)

    async def download(request: Request):
        try:
            path = request.query_params.get("path")
            if not path:
                raise ValueError("path query parameter is required")
            target = await anyio.to_thread.run_sync(files.resolve, path)
            if not await anyio.to_thread.run_sync(target.is_file):
                raise FileNotFoundError(str(target))
            return FileResponse(target, filename=target.name, media_type="application/octet-stream",
                                headers={"Cache-Control": "no-store"})
        except (OSError, ValueError) as exc:
            return failure(exc)

    return [Route("/health", health), Route("/files/upload", upload, methods=["PUT"]),
            Route("/files/download", download, methods=["GET", "HEAD"])]


def create_http_app(runtime, settings: Settings):
    mcp_app = runtime.mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        try:
            async with runtime.mcp.session_manager.run():
                yield
        finally:
            await runtime.close()

    app = Starlette(routes=[*file_routes(runtime.files), Mount("/", app=mcp_app)], lifespan=lifespan)
    return BearerAuth(app, settings)
