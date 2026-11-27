"""Safe, streaming local document extraction with O_NOFOLLOW and TOCTOU protection."""

import errno
import hashlib
import io
import os
import stat
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from orion.application.contracts import CancellationToken
from orion.application.knowledge.contracts import (
    ExtractedDocument,
    ExtractedHeader,
    ExtractedSection,
    KnowledgeLimits,
)
from orion.application.knowledge.ports import (
    DocumentExtractionError,
    DocumentExtractorPort,
    IngestionCancelledError,
)
from orion.application.tools.sensitive_paths import is_sensitive_filename
from orion.infrastructure.knowledge.epub_extractor import parse_epub_metadata_and_sections
from orion.infrastructure.knowledge.formats import (
    extract_html_sections,
    extract_rtf_sections,
    extract_xml_sections,
    iter_csv_sections,
    iter_ipynb_sections,
    iter_json_sections,
    iter_odf_sections,
    iter_pptx_sections,
    iter_xlsx_sections,
)

TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".txt", ".md", ".rst", ".log", ".tex", ".ini", ".conf"}
)
CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".java",
        ".go",
        ".rs",
        ".cs",
        ".php",
        ".rb",
        ".swift",
        ".kt",
        ".vue",
        ".sql",
        ".sh",
        ".yaml",
        ".yml",
        ".toml",
    }
)
STREAMING_TEXT_EXTENSIONS: frozenset[str] = (
    TEXT_EXTENSIONS | CODE_EXTENSIONS | frozenset({".csv", ".tsv"})
)
DOCUMENT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".epub",
        ".html",
        ".htm",
        ".pptx",
        ".xlsx",
        ".xlsm",
        ".rtf",
        ".odt",
        ".ods",
        ".xml",
        ".json",
        ".ipynb",
    }
)
SUPPORTED_EXTENSIONS: frozenset[str] = STREAMING_TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS
LEGACY_UNSUPPORTED_OFFICE: frozenset[str] = frozenset({".doc", ".xls", ".ppt"})

PROHIBITED_PATH_PREFIXES: tuple[str, ...] = ("/proc", "/sys", "/dev", "/etc/shadow", "/etc/passwd")
EXCLUDED_FOLDER_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".ssh",
        ".gnupg",
        ".aws",
        ".azure",
        ".kube",
        ".docker",
        ".gcloud",
    }
)


def validate_safe_user_path(path: Path) -> Path:
    """Validate that a user-selected path is non-sensitive and contains no prohibited segments."""
    raw = str(path)
    if "\0" in raw:
        raise DocumentExtractionError("Path contains null bytes.")
    for prefix in PROHIBITED_PATH_PREFIXES:
        if raw == prefix or raw.startswith(prefix + "/"):
            raise DocumentExtractionError(f"Access to system path {raw!r} is prohibited.")
    for part in path.parts:
        if part == "..":
            raise DocumentExtractionError(f"Path traversal ('..') is prohibited: {raw!r}")
        if is_sensitive_filename(part):
            raise DocumentExtractionError(
                f"Access to sensitive file or directory {part!r} is strictly prohibited."
            )
    return path


def _open_descriptor_nofollow(
    path: Path,
    max_bytes: int | None,
) -> tuple[io.BufferedReader, os.stat_result]:
    """Open path strictly via O_NOFOLLOW along all components and return (BufferedReader, stat)."""
    if not path.is_absolute():
        path = path.resolve()
    validate_safe_user_path(path)

    parts = path.parts
    if not parts or parts[0] != "/":
        raise DocumentExtractionError(f"Unsupported path format: {path}")

    current_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    fds_to_close = [current_fd]
    try:
        for part in parts[1:-1]:
            if is_sensitive_filename(part):
                raise DocumentExtractionError(
                    f"Access to sensitive directory {part!r} is prohibited."
                )
            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=current_fd,
                )
                fds_to_close.append(next_fd)
                current_fd = next_fd
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise DocumentExtractionError(
                        f"Symlink or non-directory component {part!r} is prohibited."
                    ) from exc
                if exc.errno == errno.ENOENT:
                    raise DocumentExtractionError(f"Path does not exist: {path}") from exc
                raise DocumentExtractionError(
                    f"Cannot open directory component {part!r}: {exc}"
                ) from exc

        filename = parts[-1]
        if is_sensitive_filename(filename):
            raise DocumentExtractionError(f"Access to sensitive file {filename!r} is prohibited.")
        try:
            file_fd = os.open(
                filename,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current_fd,
            )
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise DocumentExtractionError(
                    f"Symbolic links are prohibited during ingestion: {path}"
                ) from exc
            if exc.errno == errno.ENOENT:
                raise DocumentExtractionError(f"File does not exist: {path}") from exc
            raise DocumentExtractionError(f"Cannot open file {path}: {exc}") from exc

        try:
            st = os.fstat(file_fd)
            if not stat.S_ISREG(st.st_mode):
                raise DocumentExtractionError(f"Target is not a regular file: {path}")
            if st.st_size == 0:
                raise DocumentExtractionError(f"Document is empty: {path.name}")
            if max_bytes is not None and st.st_size > max_bytes:
                raise DocumentExtractionError(
                    f"File {path.name!r} ({st.st_size} bytes) exceeds maximum allowed size "
                    f"({max_bytes} bytes)."
                )
            fh = os.fdopen(file_fd, "rb")
            file_fd = -1  # ownership transferred to fh
            return fh, st
        finally:
            if file_fd != -1:
                os.close(file_fd)
    finally:
        for fd in fds_to_close:
            try:
                os.close(fd)
            except OSError:
                pass


def _hash_stream_and_rewind(
    fh: io.BufferedReader,
    st_before: os.stat_result,
    filename: str,
    block_size: int = 262_144,
    cancel_token: CancellationToken | None = None,
    check_binary: bool = False,
) -> str:
    """Stream-compute SHA-256 without loading entire file into RAM, checking TOCTOU."""
    hasher = hashlib.sha256()
    while True:
        if cancel_token is not None and cancel_token.is_cancelled:
            raise IngestionCancelledError("File hashing cancelled by user.")
        block = fh.read(block_size)
        if not block:
            break
        if check_binary and b"\x00" in block:
            raise DocumentExtractionError(
                f"Binary data detected in text file {filename!r}; import rejected."
            )
        hasher.update(block)

    st_after = os.fstat(fh.fileno())
    if st_after.st_size != st_before.st_size or st_after.st_mtime_ns != st_before.st_mtime_ns:
        raise DocumentExtractionError(f"File {filename!r} changed during ingestion; please retry.")
    fh.seek(0)
    return hasher.hexdigest()


def _iter_plain_text_blocks(
    fh: io.BufferedReader,
    filename: str,
    block_bytes: int,
    cancel_token: CancellationToken | None = None,
) -> Iterator[ExtractedSection]:
    """Stream UTF-8 plain text/code incrementally without loading the whole file into RAM."""
    text_stream = io.TextIOWrapper(fh, encoding="utf-8", errors="strict", newline=None)
    try:
        has_any = False
        carry = ""
        while True:
            if cancel_token is not None and cancel_token.is_cancelled:
                raise IngestionCancelledError("Text extraction cancelled by user.")
            chunk = text_stream.read(block_bytes)
            if not chunk:
                break
            data = carry + chunk
            # Split cleanly on last newline so lines aren't torn across blocks
            last_nl = data.rfind("\n")
            if last_nl != -1 and last_nl > 0:
                emit_part = data[: last_nl + 1]
                carry = data[last_nl + 1 :]
            else:
                emit_part = data
                carry = ""
            if emit_part.strip():
                has_any = True
                yield ExtractedSection(section_title=filename, text=emit_part.strip())
        if carry.strip():
            has_any = True
            yield ExtractedSection(section_title=filename, text=carry.strip())
        if not has_any:
            raise DocumentExtractionError(f"Document {filename!r} is empty.")
    except UnicodeDecodeError as exc:
        raise DocumentExtractionError(f"File {filename!r} is not valid UTF-8 text: {exc}") from exc
    finally:
        try:
            text_stream.detach()
        except Exception:  # noqa: BLE001, S110
            pass


def _iter_markdown_sections_stream(
    fh: io.BufferedReader,
    filename: str,
    cancel_token: CancellationToken | None = None,
) -> Iterator[ExtractedSection]:
    text_stream = io.TextIOWrapper(fh, encoding="utf-8", errors="strict", newline=None)
    current_title: str | None = filename
    buf: list[str] = []
    buf_chars = 0
    has_any = False
    try:
        for line in text_stream:
            if cancel_token is not None and cancel_token.is_cancelled:
                raise IngestionCancelledError("Markdown extraction cancelled by user.")
            stripped = line.strip()
            if stripped.startswith("#"):
                if buf:
                    body = "\n".join(buf).strip()
                    if body:
                        has_any = True
                        yield ExtractedSection(section_title=current_title, text=body)
                    buf.clear()
                    buf_chars = 0
                current_title = stripped.lstrip("#").strip() or current_title
            buf.append(line.rstrip("\r\n"))
            buf_chars += len(line)
            if buf_chars >= 65_536:
                body = "\n".join(buf).strip()
                if body:
                    has_any = True
                    yield ExtractedSection(section_title=current_title, text=body)
                buf.clear()
                buf_chars = 0
        if buf:
            body = "\n".join(buf).strip()
            if body:
                has_any = True
                yield ExtractedSection(section_title=current_title, text=body)
        if not has_any:
            raise DocumentExtractionError(f"Document {filename!r} is empty.")
    except UnicodeDecodeError as exc:
        raise DocumentExtractionError(f"File {filename!r} is not valid UTF-8 text: {exc}") from exc
    finally:
        try:
            text_stream.detach()
        except Exception:  # noqa: BLE001, S110
            pass


def _iter_pdf_sections(
    fh: io.BufferedReader,
    filename: str,
    cancel_token: CancellationToken | None = None,
) -> Iterator[ExtractedSection]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(fh)
    except Exception as exc:
        raise DocumentExtractionError(f"Malformed PDF document {filename!r}: {exc}") from exc

    if reader.is_encrypted:
        raise DocumentExtractionError(
            f"PDF document {filename!r} is encrypted; encrypted PDFs are not supported."
        )

    yielded = False
    for idx, page in enumerate(reader.pages, start=1):
        if cancel_token is not None and cancel_token.is_cancelled:
            raise IngestionCancelledError("PDF extraction cancelled by user.")
        try:
            page_text = (page.extract_text() or "").strip()
        except Exception as exc:
            raise DocumentExtractionError(
                f"Failed to extract page {idx} of PDF {filename!r}: {exc}"
            ) from exc
        if page_text:
            yielded = True
            yield ExtractedSection(page_number=idx, section_title=f"Page {idx}", text=page_text)
    if not yielded:
        raise DocumentExtractionError(
            f"PDF document {filename!r} contains no extractable text (OCR is not supported)."
        )


def _iter_docx_sections(
    fh: io.BufferedReader,
    filename: str,
    limits: KnowledgeLimits,
    cancel_token: CancellationToken | None = None,
) -> Iterator[ExtractedSection]:
    import zipfile

    from docx import Document

    from orion.infrastructure.knowledge.archive_security import inspect_zip_archive

    try:
        with zipfile.ZipFile(fh, "r") as zf:
            inspect_zip_archive(zf, limits, cancel_token=cancel_token)
    except DocumentExtractionError:
        raise
    except Exception as exc:
        raise DocumentExtractionError(f"Malformed DOCX archive {filename!r}: {exc}") from exc

    fh.seek(0)
    try:
        doc = Document(fh)
    except Exception as exc:
        raise DocumentExtractionError(f"Malformed DOCX document {filename!r}: {exc}") from exc

    current_title: str | None = filename
    buf: list[str] = []
    yielded = False

    for para in doc.paragraphs:
        if cancel_token is not None and cancel_token.is_cancelled:
            raise IngestionCancelledError("DOCX extraction cancelled by user.")
        txt = para.text.strip()
        if not txt:
            continue
        style_name = getattr(getattr(para, "style", None), "name", "") or ""
        if style_name.lower().startswith("heading"):
            if buf:
                yielded = True
                yield ExtractedSection(section_title=current_title, text="\n".join(buf))
                buf.clear()
            current_title = txt
        else:
            buf.append(txt)

    if buf:
        yielded = True
        yield ExtractedSection(section_title=current_title, text="\n".join(buf))

    if not yielded:
        raise DocumentExtractionError(f"DOCX document {filename!r} contains no extractable text.")


class LocalDocumentExtractor(DocumentExtractorPort):
    """Modular, streaming local extractor supporting text, code, EPUB, PDF, and Office formats."""

    def __init__(self, limits: KnowledgeLimits | None = None) -> None:
        self._limits = limits or KnowledgeLimits()

    def iter_sections(
        self,
        file_path: Path,
        cancel_token: CancellationToken | None = None,
    ) -> tuple[ExtractedHeader, Iterator[ExtractedSection]]:
        """Safely open file_path with O_NOFOLLOW and return (ExtractedHeader, section_iterator)."""
        suffix = file_path.suffix.lower()
        if suffix in LEGACY_UNSUPPORTED_OFFICE:
            raise DocumentExtractionError(
                f"Legacy binary Office format {suffix!r} is not supported; please convert to "
                ".docx/.xlsx/.pptx."
            )
        if suffix not in SUPPORTED_EXTENSIONS:
            raise DocumentExtractionError(
                f"Unsupported file format {suffix!r} for {file_path.name!r}."
            )

        is_streaming_text = suffix in STREAMING_TEXT_EXTENSIONS
        max_bytes = (
            self._limits.max_streaming_file_bytes
            if is_streaming_text
            else self._limits.max_binary_file_bytes
        )

        fh, st = _open_descriptor_nofollow(file_path, max_bytes=max_bytes)
        try:
            content_hash = _hash_stream_and_rewind(
                fh,
                st,
                file_path.name,
                block_size=self._limits.stream_read_block_bytes,
                cancel_token=cancel_token,
                check_binary=is_streaming_text,
            )
            modified_at = datetime.fromtimestamp(st.st_mtime, tz=UTC)

            title: str | None = None
            author: str | None = None
            language: str | None = None

            if suffix == ".epub":
                import zipfile

                try:
                    zf = zipfile.ZipFile(fh, "r")
                except Exception as exc:
                    raise DocumentExtractionError(
                        f"Malformed EPUB archive {file_path.name!r}: {exc}"
                    ) from exc
                meta, epub_iter = parse_epub_metadata_and_sections(
                    zf, self._limits, cancel_token=cancel_token
                )
                title = meta.get("title")
                author = meta.get("author")
                language = meta.get("language")

                def _epub_gen() -> Iterator[ExtractedSection]:
                    try:
                        yielded = False
                        for sec in epub_iter:
                            yielded = True
                            yield sec
                        if not yielded:
                            raise DocumentExtractionError(
                                f"EPUB document {file_path.name!r} contains no readable chapter text."
                            )
                    finally:
                        zf.close()
                        fh.close()

                sec_iter = _epub_gen()

            elif suffix == ".pdf":

                def _pdf_gen() -> Iterator[ExtractedSection]:
                    try:
                        yield from _iter_pdf_sections(fh, file_path.name, cancel_token)
                    finally:
                        fh.close()

                sec_iter = _pdf_gen()

            elif suffix == ".docx":

                def _docx_gen() -> Iterator[ExtractedSection]:
                    try:
                        yield from _iter_docx_sections(
                            fh, file_path.name, self._limits, cancel_token
                        )
                    finally:
                        fh.close()

                sec_iter = _docx_gen()

            elif suffix == ".pptx":

                def _pptx_gen() -> Iterator[ExtractedSection]:
                    try:
                        yield from iter_pptx_sections(
                            fh, file_path.name, self._limits, cancel_token
                        )
                    finally:
                        fh.close()

                sec_iter = _pptx_gen()

            elif suffix in (".xlsx", ".xlsm"):

                def _xlsx_gen() -> Iterator[ExtractedSection]:
                    try:
                        yield from iter_xlsx_sections(
                            fh, file_path.name, self._limits, cancel_token
                        )
                    finally:
                        fh.close()

                sec_iter = _xlsx_gen()

            elif suffix in (".odt", ".ods"):

                def _odf_gen() -> Iterator[ExtractedSection]:
                    try:
                        yield from iter_odf_sections(
                            fh, file_path.name, suffix, self._limits, cancel_token
                        )
                    finally:
                        fh.close()

                sec_iter = _odf_gen()

            elif suffix in (".csv", ".tsv"):
                delim = "\t" if suffix == ".tsv" else ","

                def _csv_gen() -> Iterator[ExtractedSection]:
                    ts = io.TextIOWrapper(fh, encoding="utf-8", errors="strict", newline="")
                    try:
                        yield from iter_csv_sections(ts, file_path.name, delim, cancel_token)
                    except UnicodeDecodeError as exc:
                        raise DocumentExtractionError(
                            f"File {file_path.name!r} is not valid UTF-8 text: {exc}"
                        ) from exc
                    finally:
                        fh.close()

                sec_iter = _csv_gen()

            elif suffix in (".md", ".rst"):

                def _md_gen() -> Iterator[ExtractedSection]:
                    try:
                        yield from _iter_markdown_sections_stream(fh, file_path.name, cancel_token)
                    finally:
                        fh.close()

                sec_iter = _md_gen()

            elif suffix in (".html", ".htm"):
                raw_text = fh.read().decode("utf-8", errors="replace")
                fh.close()
                html_title, html_secs = extract_html_sections(raw_text)
                title = html_title
                if not html_secs:
                    raise DocumentExtractionError(
                        f"HTML document {file_path.name!r} contains no readable text."
                    )
                sec_iter = iter(html_secs)

            elif suffix == ".rtf":
                raw_bytes = fh.read()
                fh.close()
                sec_iter = iter(extract_rtf_sections(raw_bytes, file_path.name))

            elif suffix == ".xml":
                raw_bytes = fh.read()
                fh.close()
                sec_iter = iter(extract_xml_sections(raw_bytes, file_path.name))

            elif suffix == ".json":
                raw_text = fh.read().decode("utf-8", errors="strict")
                fh.close()
                sec_iter = iter_json_sections(raw_text, file_path.name)

            elif suffix == ".ipynb":
                raw_text = fh.read().decode("utf-8", errors="strict")
                fh.close()
                ipynb_secs = list(iter_ipynb_sections(raw_text, file_path.name))
                if not ipynb_secs:
                    raise DocumentExtractionError(
                        f"Notebook {file_path.name!r} contains no non-empty cells."
                    )
                sec_iter = iter(ipynb_secs)

            else:
                # Streaming plain text / source code (no arbitrary 20 MB / 2M char limit!)
                def _txt_gen() -> Iterator[ExtractedSection]:
                    try:
                        yield from _iter_plain_text_blocks(
                            fh,
                            file_path.name,
                            self._limits.stream_read_block_bytes,
                            cancel_token,
                        )
                    finally:
                        fh.close()

                sec_iter = _txt_gen()

            header = ExtractedHeader(
                display_name=file_path.name,
                source_path=str(file_path),
                file_type=suffix.lstrip("."),
                file_size=st.st_size,
                content_hash=content_hash,
                modified_at=modified_at,
                title=title,
                author=author,
                language=language,
            )
            return header, sec_iter
        except Exception:
            try:
                fh.close()
            except Exception:  # noqa: BLE001, S110
                pass
            raise

    def extract(self, file_path: Path) -> ExtractedDocument:
        """Convenience wrapper collecting streamed sections into ExtractedDocument."""
        header, sec_iter = self.iter_sections(file_path)
        sections = tuple(sec_iter)
        return ExtractedDocument(
            display_name=header.display_name,
            source_path=header.source_path,
            file_type=header.file_type,
            file_size=header.file_size,
            content_hash=header.content_hash,
            modified_at=header.modified_at,
            title=header.title,
            author=header.author,
            language=header.language,
            sections=sections,
        )
