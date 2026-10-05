"""Configuration shared by the HTTP server and local stdio transport."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.environ.get(name, default))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _csv(name: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in os.environ.get(name, "").split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    token: str = field(default="", repr=False)
    workspace: Path = field(default_factory=Path.cwd)
    data_dir: Path = field(default_factory=lambda: Path.cwd() / ".fullremote")
    max_upload_bytes: int = 1024 * 1024 * 1024
    max_inline_bytes: int = 1024 * 1024
    max_log_bytes: int = 8 * 1024 * 1024
    max_jobs: int = 8
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> Settings:
        workspace = Path(os.environ.get("FULLREMOTE_WORKSPACE", Path.cwd())).expanduser().resolve()
        return cls(
            host=os.environ.get("FULLREMOTE_HOST", "127.0.0.1"),
            port=_integer("FULLREMOTE_PORT", 8765, 1, 65535),
            token=os.environ.get("FULLREMOTE_TOKEN", ""),
            workspace=workspace,
            data_dir=Path(
                os.environ.get("FULLREMOTE_DATA_DIR", workspace / ".fullremote")
            ).expanduser().resolve(),
            max_upload_bytes=_integer("FULLREMOTE_MAX_UPLOAD_BYTES", 1024**3, 1, 1024**5),
            max_inline_bytes=_integer("FULLREMOTE_MAX_INLINE_BYTES", 1024**2, 1, 4 * 1024**2),
            max_log_bytes=_integer("FULLREMOTE_MAX_LOG_BYTES", 8 * 1024**2, 1024, 1024**3),
            max_jobs=_integer("FULLREMOTE_MAX_JOBS", 8, 1, 64),
            allowed_hosts=_csv("FULLREMOTE_ALLOWED_HOSTS"),
            allowed_origins=_csv("FULLREMOTE_ALLOWED_ORIGINS"),
        )

    def validate_http(self) -> None:
        if len(self.token) < 32 or not self.token.isascii() or any(c.isspace() for c in self.token):
            raise ValueError(
                "FULLREMOTE_TOKEN must contain at least 32 ASCII characters without whitespace. "
                "Generate one with: fullremote-mcp token"
            )
        if self.host in {"0.0.0.0", "::"} and not self.allowed_hosts:
            raise ValueError(
                "Set FULLREMOTE_ALLOWED_HOSTS to the VM's reachable hostname/IP and port "
                "when listening on all interfaces (for example: windows-vm:8765)."
            )

    @property
    def trusted_hosts(self) -> list[str]:
        hosts = ["localhost:*", "127.0.0.1:*", "[::1]:*"]
        if self.host not in {"0.0.0.0", "::", "127.0.0.1", "localhost"}:
            host = f"[{self.host}]" if ":" in self.host else self.host
            hosts.append(f"{host}:*")
        return list(dict.fromkeys(host.lower() for host in [*hosts, *self.allowed_hosts]))

    @property
    def trusted_origins(self) -> list[str]:
        return [
            "http://localhost:*",
            "http://127.0.0.1:*",
            "http://[::1]:*",
            *self.allowed_origins,
        ]
