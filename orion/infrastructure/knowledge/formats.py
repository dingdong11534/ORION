"""Modular format extractors for HTML, PPTX, XLSX/XLSM, CSV/TSV, RTF, ODT/ODS, XML, JSON, IPYNB."""

import csv
import io
import json
import zipfile
from collections.abc import Iterator
from html.parser import HTMLParser
from typing import Any

from orion.application.contracts import CancellationToken
from orion.application.knowledge.contracts import ExtractedSection, KnowledgeLimits
from orion.application.knowledge.ports import DocumentExtractionError, IngestionCancelledError
from orion.infrastructure.knowledge.archive_security import (
    inspect_zip_archive,
    parse_xml_safe,
)


class _HTMLContentParser(HTMLParser):
    """Extract title, headings, and readable text from HTML while ignoring script/style."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._in_heading = False
        self._title_buf: list[str] = []
        self._heading_buf: list[str] = []
        self._text_buf: list[str] = []
        self._current_heading: str | None = None
        self.title: str | None = None
        self.sections: list[ExtractedSection] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low = tag.lower()
        if low in ("script", "style", "noscript"):
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if low == "title":
            self._in_title = True
            self._title_buf.clear()
        elif low in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush()
            self._in_heading = True
            self._heading_buf.clear()
        elif low in ("p", "div", "li", "br", "tr"):
            self._text_buf.append("\n")

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        if low in ("script", "style", "noscript"):
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return
        if low == "title" and self._in_title:
            self._in_title = False
            t = " ".join("".join(self._title_buf).split())
            if t:
                self.title = t
        elif low in ("h1", "h2", "h3", "h4", "h5", "h6") and self._in_heading:
            self._in_heading = False
            h = " ".join("".join(self._heading_buf).split())
            if h:
                self._current_heading = h
            self._text_buf.append("\n")
        elif low in ("p", "div", "li", "tr"):
            self._text_buf.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self._title_buf.append(data)
            return
        if self._in_heading:
            self._heading_buf.append(data)
        self._text_buf.append(data)

    def _flush(self) -> None:
        body = "\n".join(
            line.strip() for line in "".join(self._text_buf).splitlines() if line.strip()
        )
        if body:
            self.sections.append(
                ExtractedSection(section_title=self._current_heading or self.title, text=body)
            )
        self._text_buf.clear()

    def finish(self) -> tuple[str | None, list[ExtractedSection]]:
        self._flush()
        return self.title, self.sections


def extract_html_sections(text: str) -> tuple[str | None, list[ExtractedSection]]:
    parser = _HTMLContentParser()
    parser.feed(text)
    return parser.finish()


def iter_pptx_sections(
    fh: io.BufferedReader | io.BytesIO,
    filename: str,
    limits: KnowledgeLimits,
    cancel_token: CancellationToken | None = None,
) -> Iterator[ExtractedSection]:
    from pptx import Presentation

    try:
        with zipfile.ZipFile(fh, "r") as zf:
            inspect_zip_archive(zf, limits, cancel_token=cancel_token)
    except DocumentExtractionError:
        raise
    except Exception as exc:
        raise DocumentExtractionError(f"Malformed PPTX archive {filename!r}: {exc}") from exc

    fh.seek(0)
    try:
        prs = Presentation(fh)
    except Exception as exc:
        raise DocumentExtractionError(f"Malformed PPTX presentation {filename!r}: {exc}") from exc

    for idx, slide in enumerate(prs.slides, start=1):
        if cancel_token is not None and cancel_token.is_cancelled:
            raise IngestionCancelledError("PPTX extraction cancelled by user.")
        title_text: str | None = None
        if slide.shapes.title is not None and slide.shapes.title.text:
            title_text = " ".join(slide.shapes.title.text.split())

        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                txt = "\n".join(
                    p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip()
                )
                if txt:
                    parts.append(txt)
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [" ".join(c.text.split()) for c in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells))

        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"[Speaker Notes]\n{notes}")

        body = "\n".join(parts).strip()
        if body:
            yield ExtractedSection(
                page_number=idx,
                section_title=f"Slide {idx}: {title_text}" if title_text else f"Slide {idx}",
                text=body,
            )


def iter_xlsx_sections(
    fh: io.BufferedReader | io.BytesIO,
    filename: str,
    limits: KnowledgeLimits,
    cancel_token: CancellationToken | None = None,
) -> Iterator[ExtractedSection]:
    import openpyxl

    try:
        with zipfile.ZipFile(fh, "r") as zf:
            inspect_zip_archive(zf, limits, cancel_token=cancel_token)
    except DocumentExtractionError:
        raise
    except Exception as exc:
        raise DocumentExtractionError(f"Malformed XLSX/XLSM archive {filename!r}: {exc}") from exc

    fh.seek(0)
    wb = None
    try:
        wb = openpyxl.load_workbook(fh, read_only=True, data_only=True)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            buf: list[str] = []
            start_row = 1
            for row_idx, row in enumerate(ws.iter_rows(values_only=False), start=1):
                if cancel_token is not None and cancel_token.is_cancelled:
                    raise IngestionCancelledError("XLSX extraction cancelled by user.")
                cell_strs: list[str] = []
                for cell in row:
                    val = cell.value
                    if val is not None and str(val).strip():
                        coord = getattr(cell, "coordinate", f"R{row_idx}")
                        cell_strs.append(f"{coord}={val}")
                if cell_strs:
                    buf.append(" | ".join(cell_strs))
                if len(buf) >= 40:
                    yield ExtractedSection(
                        page_number=start_row,
                        section_title=f"Sheet {sheet_name} (Rows {start_row}-{row_idx})",
                        text="\n".join(buf),
                    )
                    buf.clear()
                    start_row = row_idx + 1
            if buf:
                yield ExtractedSection(
                    page_number=start_row,
                    section_title=f"Sheet {sheet_name} (Rows {start_row}+)",
                    text="\n".join(buf),
                )
    except IngestionCancelledError:
        raise
    except Exception as exc:
        raise DocumentExtractionError(f"Malformed XLSX/XLSM workbook {filename!r}: {exc}") from exc
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:  # noqa: BLE001, S110
                pass


def iter_csv_sections(
    text_stream: io.TextIOBase,
    filename: str,
    default_delimiter: str = ",",
    cancel_token: CancellationToken | None = None,
) -> Iterator[ExtractedSection]:
    sample = text_stream.read(4096)
    text_stream.seek(0)
    delimiter = default_delimiter
    if sample and default_delimiter == ",":
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ","

    reader = csv.reader(text_stream, delimiter=delimiter)
    headers: list[str] | None = None
    buf: list[str] = []
    start_row = 1

    for row_idx, row in enumerate(reader, start=1):
        if cancel_token is not None and cancel_token.is_cancelled:
            raise IngestionCancelledError("CSV extraction cancelled by user.")
        if not any(cell.strip() for cell in row):
            continue
        if headers is None:
            headers = [h.strip() or f"col_{i + 1}" for i, h in enumerate(row)]
            continue
        pairs = []
        for idx, val in enumerate(row):
            v = val.strip()
            if not v:
                continue
            key = headers[idx] if idx < len(headers) else f"col_{idx + 1}"
            pairs.append(f"{key}: {v}")
        if pairs:
            buf.append(f"Row {row_idx}: " + ", ".join(pairs))
        if len(buf) >= 40:
            yield ExtractedSection(
                page_number=start_row,
                section_title=f"{filename} (Rows {start_row}-{row_idx})",
                text="\n".join(buf),
            )
            buf.clear()
            start_row = row_idx + 1

    if buf:
        yield ExtractedSection(
            page_number=start_row,
            section_title=f"{filename} (Rows {start_row}+)",
            text="\n".join(buf),
        )
    elif headers is not None and start_row == 1:
        yield ExtractedSection(section_title=filename, text="Headers: " + ", ".join(headers))


def extract_rtf_sections(raw_bytes: bytes, filename: str) -> list[ExtractedSection]:
    from striprtf.striprtf import rtf_to_text

    try:
        decoded = raw_bytes.decode("utf-8", errors="replace")
        plain = rtf_to_text(decoded).strip()
    except Exception as exc:
        raise DocumentExtractionError(f"Malformed RTF document {filename!r}: {exc}") from exc
    if not plain:
        raise DocumentExtractionError(f"RTF document {filename!r} contains no readable text.")
    return [ExtractedSection(section_title=filename, text=plain)]


def iter_odf_sections(
    fh: io.BufferedReader | io.BytesIO,
    filename: str,
    suffix: str,
    limits: KnowledgeLimits,
    cancel_token: CancellationToken | None = None,
) -> Iterator[ExtractedSection]:
    from odf import teletype
    from odf.opendocument import load as odf_load
    from odf.table import Table, TableCell, TableRow

    try:
        with zipfile.ZipFile(fh, "r") as zf:
            inspect_zip_archive(zf, limits, cancel_token=cancel_token)
    except DocumentExtractionError:
        raise
    except Exception as exc:
        raise DocumentExtractionError(
            f"Malformed {suffix.upper()} archive {filename!r}: {exc}"
        ) from exc

    fh.seek(0)
    try:
        doc = odf_load(fh)
    except Exception as exc:
        raise DocumentExtractionError(
            f"Malformed {suffix.upper()} document {filename!r}: {exc}"
        ) from exc

    if suffix == ".odt":
        current_heading: str | None = filename
        buf: list[str] = []
        for elem in doc.text.childNodes:
            if cancel_token is not None and cancel_token.is_cancelled:
                raise IngestionCancelledError("ODT extraction cancelled by user.")
            tag = getattr(elem, "tagName", "")
            if tag == "text:h":
                if buf:
                    yield ExtractedSection(section_title=current_heading, text="\n".join(buf))
                    buf.clear()
                h_txt = teletype.extractText(elem).strip()
                if h_txt:
                    current_heading = h_txt
            elif tag == "text:p":
                p_txt = teletype.extractText(elem).strip()
                if p_txt:
                    buf.append(p_txt)
            elif tag == "table:table":
                for row in elem.getElementsByType(TableRow):
                    cells = [
                        teletype.extractText(c).strip() for c in row.getElementsByType(TableCell)
                    ]
                    if any(cells):
                        buf.append(" | ".join(c for c in cells if c))
        if buf:
            yield ExtractedSection(section_title=current_heading, text="\n".join(buf))
    else:  # .ods
        for table in doc.spreadsheet.getElementsByType(Table):
            sheet_name = table.getAttribute("name") or "Sheet"
            buf = []
            start_row = 1
            for r_idx, row in enumerate(table.getElementsByType(TableRow), start=1):
                if cancel_token is not None and cancel_token.is_cancelled:
                    raise IngestionCancelledError("ODS extraction cancelled by user.")
                cells = [teletype.extractText(c).strip() for c in row.getElementsByType(TableCell)]
                if any(cells):
                    buf.append(f"Row {r_idx}: " + " | ".join(c for c in cells if c))
                if len(buf) >= 40:
                    yield ExtractedSection(
                        page_number=start_row,
                        section_title=f"Sheet {sheet_name} (Rows {start_row}-{r_idx})",
                        text="\n".join(buf),
                    )
                    buf.clear()
                    start_row = r_idx + 1
            if buf:
                yield ExtractedSection(
                    page_number=start_row,
                    section_title=f"Sheet {sheet_name} (Rows {start_row}+)",
                    text="\n".join(buf),
                )


def extract_xml_sections(raw_bytes: bytes, filename: str) -> list[ExtractedSection]:
    root = parse_xml_safe(raw_bytes, f"XML document {filename!r}")
    lines: list[str] = []

    def _walk(elem: Any, prefix: str) -> None:
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        path = f"{prefix}/{tag}" if prefix else tag
        txt = (elem.text or "").strip()
        attrs = " ".join(f"{k}={v!r}" for k, v in elem.attrib.items())
        if txt or attrs:
            entry = f"{path}"
            if attrs:
                entry += f" [{attrs}]"
            if txt:
                entry += f": {txt}"
            lines.append(entry)
        for child in elem:
            _walk(child, path)

    _walk(root, "")
    if not lines:
        raise DocumentExtractionError(f"XML document {filename!r} contains no readable text.")
    return [ExtractedSection(section_title=filename, text="\n".join(lines))]


def iter_json_sections(text: str, filename: str) -> Iterator[ExtractedSection]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DocumentExtractionError(f"Malformed JSON document {filename!r}: {exc}") from exc

    lines: list[str] = []

    def _flatten(obj: Any, path: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                _flatten(v, f"{path}.{k}" if path else str(k))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _flatten(v, f"{path}[{i}]")
        else:
            lines.append(f"{path}: {obj}")

    _flatten(data, "")
    if not lines:
        raise DocumentExtractionError(f"JSON document {filename!r} is empty.")
    for i in range(0, len(lines), 80):
        yield ExtractedSection(
            section_title=f"{filename} (keys {i + 1}-{min(i + 80, len(lines))})",
            text="\n".join(lines[i : i + 80]),
        )


def iter_ipynb_sections(
    text: str,
    filename: str,
    include_outputs: bool = False,
) -> Iterator[ExtractedSection]:
    try:
        nb = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DocumentExtractionError(f"Malformed Jupyter notebook {filename!r}: {exc}") from exc

    cells = nb.get("cells")
    if not isinstance(cells, list):
        raise DocumentExtractionError(
            f"Invalid Jupyter notebook {filename!r}: missing 'cells' list."
        )

    for idx, cell in enumerate(cells):
        if not isinstance(cell, dict):
            continue
        ctype = str(cell.get("cell_type") or "unknown")
        src = cell.get("source", "")
        src_text = "".join(src) if isinstance(src, list) else str(src)
        src_text = src_text.strip()
        if not src_text:
            continue
        parts = [src_text]
        if include_outputs and ctype == "code" and isinstance(cell.get("outputs"), list):
            for out in cell["outputs"][:5]:
                if isinstance(out, dict) and "text" in out:
                    ot = "".join(out["text"]) if isinstance(out["text"], list) else str(out["text"])
                    if ot.strip():
                        parts.append(f"[Output]\n{ot.strip()}")
        yield ExtractedSection(
            page_number=idx + 1,
            section_title=f"Cell {idx} ({ctype})",
            text="\n".join(parts),
        )
