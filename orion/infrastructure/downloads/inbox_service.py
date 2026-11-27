"""Bounded, non-executing staging service for ~/Downloads/ORION-Inbox/."""

import hashlib
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

_DISALLOWED_EXTENSIONS = frozenset(
    {
        ".appimage",
        ".bat",
        ".bin",
        ".cmd",
        ".deb",
        ".desktop",
        ".dll",
        ".exe",
        ".msi",
        ".py",
        ".rpm",
        ".sh",
        ".so",
    }
)

_DISALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/x-executable",
        "application/x-msdownload",
        "application/x-sh",
        "application/x-shellscript",
        "application/x-sharedlib",
        "text/x-python",
        "text/x-shellscript",
    }
)


class InboxPolicyError(ValueError):
    """Raised when a file violates ORION-Inbox staging policy."""


@dataclass(frozen=True, slots=True)
class StagedFileRecord:
    """Metadata for a file safely staged in ORION-Inbox."""

    path: Path
    original_filename: str
    size_bytes: int
    content_type: str
    sha256: str


class OrionInboxService:
    """Stages downloaded artifacts safely into ~/Downloads/ORION-Inbox/ without executing them."""

    def __init__(
        self,
        *,
        inbox_dir: Path | str | None = None,
        max_file_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        if inbox_dir is not None:
            self._inbox_dir = Path(inbox_dir).expanduser().resolve()
        else:
            self._inbox_dir = (Path.home() / "Downloads" / "ORION-Inbox").resolve()
        self._max_file_bytes = max_file_bytes

    @property
    def inbox_dir(self) -> Path:
        return self._inbox_dir

    def ensure_inbox_dir(self) -> Path:
        self._inbox_dir.mkdir(parents=True, exist_ok=True)
        if stat.S_IMODE(self._inbox_dir.stat().st_mode) != 0o700:
            self._inbox_dir.chmod(0o700)
        return self._inbox_dir

    def stage_bytes(
        self,
        data: bytes,
        filename: str,
        *,
        content_type: str = "application/octet-stream",
    ) -> StagedFileRecord:
        if len(data) > self._max_file_bytes:
            raise InboxPolicyError(
                f"File size {len(data)} exceeds limit of {self._max_file_bytes} bytes."
            )

        normalized_ct = (content_type or "application/octet-stream").split(";")[0].strip().lower()
        if normalized_ct in _DISALLOWED_CONTENT_TYPES:
            raise InboxPolicyError(f"Blocked executable content type: {normalized_ct!r}")

        base_name = Path(filename or "download.bin").name
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(base_name).stem).strip("._") or "file"
        suffix = Path(base_name).suffix.lower()
        if suffix in _DISALLOWED_EXTENSIONS:
            raise InboxPolicyError(f"Blocked executable extension: {suffix!r}")

        self.ensure_inbox_dir()
        unique_name = f"{safe_stem}_{uuid.uuid4().hex[:8]}{suffix}"
        dest = (self._inbox_dir / unique_name).resolve()
        if dest.parent != self._inbox_dir:
            raise InboxPolicyError("Path traversal blocked.")

        dest.write_bytes(data)
        dest.chmod(0o600)

        digest = hashlib.sha256(data).hexdigest()
        return StagedFileRecord(
            path=dest,
            original_filename=base_name,
            size_bytes=len(data),
            content_type=normalized_ct,
            sha256=digest,
        )

    def list_staged_files(self) -> list[Path]:
        if not self._inbox_dir.exists():
            return []
        return sorted(p for p in self._inbox_dir.iterdir() if p.is_file())
