"""Archive inspection, ZIP-bomb guards, and safe XML parsing for document ingestion."""

import posixpath
import stat
import xml.etree.ElementTree as ET
import zipfile

from orion.application.contracts import CancellationToken
from orion.application.knowledge.contracts import KnowledgeLimits
from orion.application.knowledge.ports import DocumentExtractionError, IngestionCancelledError


def validate_archive_member_name(name: str) -> str:
    """Validate that a ZIP member name contains no traversal or absolute path components."""
    if not name or "\0" in name:
        raise DocumentExtractionError("Archive contains an empty or null-byte member name.")
    if name.startswith(("/", "\\")):
        raise DocumentExtractionError(f"Archive contains absolute member path: {name!r}")
    norm = name.replace("\\", "/")
    parts = norm.split("/")
    if ".." in parts:
        raise DocumentExtractionError(f"Archive contains path traversal ('..'): {name!r}")
    return posixpath.normpath(norm)


def inspect_zip_archive(
    zf: zipfile.ZipFile,
    limits: KnowledgeLimits,
    cancel_token: CancellationToken | None = None,
) -> list[zipfile.ZipInfo]:
    """Validate all ZIP entries against traversal, symlinks, entry count, and ZIP-bomb bounds."""
    infos = zf.infolist()
    if len(infos) > limits.max_archive_entries:
        raise DocumentExtractionError(
            f"Archive entry count ({len(infos)}) exceeds safety limit ({limits.max_archive_entries})."
        )

    total_uncompressed = 0
    for info in infos:
        if cancel_token is not None and cancel_token.is_cancelled:
            raise IngestionCancelledError("Archive inspection cancelled by user.")

        validate_archive_member_name(info.filename)

        # Reject symlinks stored inside ZIP archives
        unix_mode = (info.external_attr >> 16) & 0o170000
        if unix_mode == stat.S_IFLNK:
            raise DocumentExtractionError(
                f"Archive contains prohibited symbolic link entry: {info.filename!r}"
            )

        if info.file_size < 0 or info.compress_size < 0:
            raise DocumentExtractionError(f"Invalid archive member size: {info.filename!r}")

        total_uncompressed += info.file_size
        if total_uncompressed > limits.max_archive_uncompressed_bytes:
            raise DocumentExtractionError(
                f"Archive declared uncompressed size ({total_uncompressed} bytes) exceeds "
                f"safety limit ({limits.max_archive_uncompressed_bytes} bytes)."
            )

        if info.file_size > 1_048_576:
            if info.compress_size == 0:
                raise DocumentExtractionError(
                    f"Suspicious zero compressed size for large member {info.filename!r}."
                )
            ratio = info.file_size / info.compress_size
            if ratio > limits.max_archive_compression_ratio:
                raise DocumentExtractionError(
                    f"Archive member {info.filename!r} compression ratio ({ratio:.1f}x) "
                    f"exceeds ZIP-bomb threshold ({limits.max_archive_compression_ratio:.1f}x)."
                )
    return infos


def read_zip_member_bounded(
    zf: zipfile.ZipFile,
    member_name: str,
    max_bytes: int,
) -> bytes:
    """Read a single ZIP member safely without extracting to disk, enforcing actual byte cap."""
    with zf.open(member_name, "r") as fh:
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise DocumentExtractionError(
            f"Decompressed archive member {member_name!r} exceeds safety limit ({max_bytes} bytes)."
        )
    return data


def parse_xml_safe(raw_bytes: bytes, context: str = "XML") -> ET.Element:
    """Parse XML bytes while rejecting DOCTYPE/ENTITY declarations (XXE/billion-laughs guard)."""
    head = raw_bytes[:8192].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise DocumentExtractionError(f"Unsafe DTD/ENTITY declaration rejected in {context}.")
    try:
        return ET.fromstring(raw_bytes)
    except ET.ParseError as exc:
        raise DocumentExtractionError(f"Malformed {context}: {exc}") from exc
