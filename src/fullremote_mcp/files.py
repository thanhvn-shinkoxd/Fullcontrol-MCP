"""Bounded inline transfers and atomic, streaming uploads."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlencode


class UploadTooLarge(ValueError):
    pass


class ChecksumMismatch(ValueError):
    pass


class AtomicUpload:
    def __init__(
        self, target: Path, *, overwrite: bool, limit: int, expected_sha256: str | None = None
    ) -> None:
        if expected_sha256 and not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        if target.exists() and not overwrite:
            raise FileExistsError(str(target))
        if target.is_dir():
            raise IsADirectoryError(str(target))
        self.target = target
        self.overwrite = overwrite
        self.limit = limit
        self.expected_sha256 = expected_sha256.lower() if expected_sha256 else None
        self.size = 0
        self.digest = hashlib.sha256()
        self.temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.part"
        # Inherit the destination directory's ACL on Windows, including restricted-token access.
        mode = 0o666 if os.name == "nt" else 0o600
        descriptor = os.open(self.temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), mode)
        self._file: BinaryIO = os.fdopen(descriptor, "wb")
        self._finished = False

    def write(self, chunk: bytes) -> None:
        if self._finished or self._file.closed:
            raise ValueError("Upload is already closed")
        if self.size + len(chunk) > self.limit:
            raise UploadTooLarge(f"Upload exceeds {self.limit} bytes")
        self._file.write(chunk)
        self.digest.update(chunk)
        self.size += len(chunk)

    def finish(self) -> dict:
        if self._finished:
            raise ValueError("Upload is already complete")
        checksum = self.digest.hexdigest()
        if self.expected_sha256 and checksum != self.expected_sha256:
            raise ChecksumMismatch("Uploaded content does not match sha256")
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        if self.overwrite:
            os.replace(self.temporary, self.target)
        elif os.name == "nt":
            # Windows rename fails atomically if another upload created the destination.
            os.rename(self.temporary, self.target)
        else:
            os.link(self.temporary, self.target)
            self.temporary.unlink()
        self._finished = True
        return {"path": str(self.target), "bytes": self.size, "sha256": checksum}

    def abort(self) -> None:
        self._file.close()
        if not self._finished:
            self.temporary.unlink(missing_ok=True)


class FileService:
    def __init__(self, workspace: Path, max_inline_bytes: int, max_upload_bytes: int) -> None:
        self.workspace = workspace.resolve()
        self.max_inline_bytes = max_inline_bytes
        self.max_upload_bytes = max_upload_bytes

    def resolve(self, path: str) -> Path:
        if "\x00" in path:
            raise ValueError("Path cannot contain a null character")
        target = Path(os.path.expandvars(path)).expanduser()
        if not target.is_absolute():
            target = self.workspace / target
        return target.resolve()

    def list_directory(self, path: str = ".", offset: int = 0, limit: int = 200) -> dict:
        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("offset must be nonnegative and limit must be between 1 and 1000")
        directory = self.resolve(path)
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name.casefold())
        output = []
        for entry in entries[offset : offset + limit]:
            try:
                info = entry.stat()
                output.append(
                    {
                        "name": entry.name,
                        "path": str(entry),
                        "is_directory": entry.is_dir(),
                        "is_symlink": entry.is_symlink(),
                        "bytes": info.st_size,
                        "modified": info.st_mtime,
                    }
                )
            except OSError as exc:
                output.append({"name": entry.name, "error": str(exc)})
        return {
            "path": str(directory),
            "entries": output,
            "total": len(entries),
            "next_offset": offset + len(output) if offset + len(output) < len(entries) else None,
        }

    def download(self, path: str, offset: int = 0, max_bytes: int = 65536) -> dict:
        if offset < 0 or max_bytes < 1:
            raise ValueError("offset must be >= 0 and max_bytes must be positive")
        max_bytes = min(max_bytes, self.max_inline_bytes)
        target = self.resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"Not a regular file: {target}")
        with target.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            stream.seek(offset)
            data = stream.read(max_bytes)
        return {
            "path": str(target),
            "offset": offset,
            "next_offset": offset + len(data),
            "total_bytes": size,
            "eof": offset + len(data) >= size,
            "content_base64": base64.b64encode(data).decode("ascii"),
        }

    def begin_upload(
        self, path: str, overwrite: bool = False, sha256: str | None = None
    ) -> AtomicUpload:
        return AtomicUpload(
            self.resolve(path),
            overwrite=overwrite,
            limit=self.max_upload_bytes,
            expected_sha256=sha256,
        )

    def upload(
        self, path: str, content_base64: str, overwrite: bool = False, sha256: str | None = None
    ) -> dict:
        if len(content_base64) > 4 * ((self.max_inline_bytes + 2) // 3):
            raise UploadTooLarge("Use the HTTP upload endpoint for large files")
        content = base64.b64decode(content_base64, validate=True)
        if len(content) > self.max_inline_bytes:
            raise UploadTooLarge("Use the HTTP upload endpoint for large files")
        upload = self.begin_upload(path, overwrite, sha256)
        try:
            upload.write(content)
            return upload.finish()
        finally:
            upload.abort()

    def read_text(self, path: str, max_bytes: int = 65536) -> dict:
        result = self.download(path, max_bytes=max_bytes)
        raw = base64.b64decode(result.pop("content_base64"))
        # A truncated read can end halfway through a UTF-8 character.
        import codecs

        decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        result["text"] = decoder.decode(raw, final=result["eof"])
        result["next_offset"] -= len(decoder.getstate()[0])
        return result

    def write_text(self, path: str, text: str, overwrite: bool = False) -> dict:
        raw = text.encode("utf-8")
        if len(raw) > self.max_inline_bytes:
            raise UploadTooLarge("Use the HTTP upload endpoint for large files")
        return self.upload(path, base64.b64encode(raw).decode("ascii"), overwrite)

    def transfer_info(self, path: str) -> dict:
        query = urlencode({"path": str(self.resolve(path))})
        return {
            "upload": {"method": "PUT", "path": f"/files/upload?{query}"},
            "download": {"method": "GET", "path": f"/files/download?{query}"},
            "authorization": "Use the same Authorization: Bearer header as /mcp",
            "overwrite": "Add &overwrite=true to explicitly replace an existing file",
            "checksum": "Optional X-Content-SHA256 header on upload",
            "max_upload_bytes": self.max_upload_bytes,
        }
