"""Dedicated EPUB2 and EPUB3 streaming extractor using stdlib ZIP/XML/HTML."""

import posixpath
import zipfile
from collections.abc import Iterator
from html.parser import HTMLParser

from orion.application.contracts import CancellationToken
from orion.application.knowledge.contracts import ExtractedSection, KnowledgeLimits
from orion.application.knowledge.ports import DocumentExtractionError, IngestionCancelledError
from orion.infrastructure.knowledge.archive_security import (
    inspect_zip_archive,
    parse_xml_safe,
    read_zip_member_bounded,
)

_FONT_OBFUSCATION_ALGS = frozenset(
    {
        "http://www.idpf.org/2008/embedding",
        "http://ns.adobe.com/pdf/enc#rc",
    }
)


class _XHTMLTextExtractor(HTMLParser):
    """Extract readable text and headings from an EPUB XHTML content document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_heading = False
        self._heading_buf: list[str] = []
        self._text_buf: list[str] = []
        self.sections: list[tuple[str | None, str]] = []
        self._current_heading: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low = tag.lower()
        if low in ("script", "style", "noscript"):
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if low in ("h1", "h2", "h3", "h4"):
            self._flush_section()
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
        if low in ("h1", "h2", "h3", "h4") and self._in_heading:
            self._in_heading = False
            heading = " ".join("".join(self._heading_buf).split())
            if heading:
                self._current_heading = heading
            self._text_buf.append("\n")
        elif low in ("p", "div", "li", "tr"):
            self._text_buf.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_heading:
            self._heading_buf.append(data)
        self._text_buf.append(data)

    def _flush_section(self) -> None:
        body = "\n".join(
            line.strip() for line in "".join(self._text_buf).splitlines() if line.strip()
        )
        if body:
            self.sections.append((self._current_heading, body))
        self._text_buf.clear()

    def finish(self) -> list[tuple[str | None, str]]:
        self._flush_section()
        return self.sections


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _check_drm(zf: zipfile.ZipFile, member_set: set[str], limits: KnowledgeLimits) -> None:
    if "META-INF/rights.xml" in member_set:
        raise DocumentExtractionError(
            "EPUB appears to be DRM-protected (META-INF/rights.xml present); DRM-protected books are not supported."
        )
    if "META-INF/encryption.xml" in member_set:
        raw = read_zip_member_bounded(
            zf, "META-INF/encryption.xml", min(limits.max_binary_file_bytes, 1_048_576)
        )
        root = parse_xml_safe(raw, "EPUB encryption.xml")
        for elem in root.iter():
            if _local_name(elem.tag) == "EncryptionMethod":
                alg = elem.attrib.get("Algorithm", "")
                if alg and alg not in _FONT_OBFUSCATION_ALGS:
                    raise DocumentExtractionError(
                        "EPUB content is encrypted or DRM-protected; DRM-protected books are not supported."
                    )


def _parse_ncx_toc(raw_bytes: bytes, opf_dir: str) -> dict[str, str]:
    root = parse_xml_safe(raw_bytes, "EPUB NCX")
    toc_map: dict[str, str] = {}
    for nav_point in root.iter():
        if _local_name(nav_point.tag) != "navPoint":
            continue
        label_text = ""
        src = ""
        for child in nav_point:
            lname = _local_name(child.tag)
            if lname == "navLabel":
                label_text = " ".join("".join(child.itertext()).split())
            elif lname == "content":
                src = child.attrib.get("src", "").split("#", 1)[0]
        if label_text and src:
            resolved = (
                posixpath.normpath(posixpath.join(opf_dir, src))
                if opf_dir
                else posixpath.normpath(src)
            )
            toc_map.setdefault(resolved, label_text)
    return toc_map


def _parse_nav_toc(raw_bytes: bytes, nav_dir: str) -> dict[str, str]:
    class _NavParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.in_toc_nav = False
            self.nav_depth = 0
            self.in_a = False
            self.href = ""
            self.label_parts: list[str] = []
            self.links: list[tuple[str, str]] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            low = tag.lower()
            attr_map = dict(attrs)
            if low == "nav":
                etype = (attr_map.get("epub:type") or attr_map.get("type") or "").lower()
                if "toc" in etype or not self.links:
                    self.in_toc_nav = True
                    self.nav_depth += 1
            elif self.in_toc_nav and low == "a":
                self.in_a = True
                self.href = (attr_map.get("href") or "").split("#", 1)[0]
                self.label_parts.clear()

        def handle_endtag(self, tag: str) -> None:
            low = tag.lower()
            if low == "nav" and self.in_toc_nav:
                self.nav_depth -= 1
                if self.nav_depth <= 0:
                    self.in_toc_nav = False
            elif low == "a" and self.in_a:
                self.in_a = False
                label = " ".join("".join(self.label_parts).split())
                if self.href and label:
                    self.links.append((self.href, label))

        def handle_data(self, data: str) -> None:
            if self.in_a:
                self.label_parts.append(data)

    parser = _NavParser()
    parser.feed(raw_bytes.decode("utf-8", errors="replace"))
    toc_map: dict[str, str] = {}
    for href, label in parser.links:
        resolved = (
            posixpath.normpath(posixpath.join(nav_dir, href))
            if nav_dir
            else posixpath.normpath(href)
        )
        toc_map.setdefault(resolved, label)
    return toc_map


def parse_epub_metadata_and_sections(
    zf: zipfile.ZipFile,
    limits: KnowledgeLimits,
    cancel_token: CancellationToken | None = None,
) -> tuple[dict[str, str | None], Iterator[ExtractedSection]]:
    """Inspect EPUB2/EPUB3 container, OPF metadata, TOC, and stream sections in spine order."""
    infos = inspect_zip_archive(zf, limits, cancel_token=cancel_token)
    member_set = {posixpath.normpath(i.filename.replace("\\", "/")) for i in infos}

    _check_drm(zf, member_set, limits)

    if "META-INF/container.xml" not in member_set:
        raise DocumentExtractionError("Invalid EPUB archive: missing META-INF/container.xml.")

    container_raw = read_zip_member_bounded(zf, "META-INF/container.xml", 1_048_576)
    container_xml = parse_xml_safe(container_raw, "EPUB container.xml")

    opf_path: str | None = None
    for elem in container_xml.iter():
        if _local_name(elem.tag) == "rootfile" and elem.attrib.get("full-path"):
            opf_path = posixpath.normpath(elem.attrib["full-path"])
            break
    if not opf_path or opf_path not in member_set:
        raise DocumentExtractionError("Invalid EPUB archive: missing OPF package document.")

    opf_raw = read_zip_member_bounded(zf, opf_path, 8 * 1024 * 1024)
    opf_xml = parse_xml_safe(opf_raw, "EPUB OPF")
    opf_dir = posixpath.dirname(opf_path)

    title: str | None = None
    author: str | None = None
    language: str | None = None
    manifest: dict[str, tuple[str, str, str]] = {}  # id -> (resolved_href, media_type, properties)
    spine_ids: list[str] = []
    ncx_id: str | None = None

    for child in opf_xml:
        lname = _local_name(child.tag)
        if lname == "metadata":
            for m in child.iter():
                ml = _local_name(m.tag)
                txt = " ".join("".join(m.itertext()).split())
                if not txt:
                    continue
                if ml == "title" and title is None:
                    title = txt
                elif ml == "creator" and author is None:
                    author = txt
                elif ml == "language" and language is None:
                    language = txt
        elif lname == "manifest":
            for item in child:
                if _local_name(item.tag) != "item":
                    continue
                iid = item.attrib.get("id", "")
                href = item.attrib.get("href", "")
                mtype = item.attrib.get("media-type", "")
                props = item.attrib.get("properties", "")
                if iid and href:
                    resolved = (
                        posixpath.normpath(posixpath.join(opf_dir, href))
                        if opf_dir
                        else posixpath.normpath(href)
                    )
                    manifest[iid] = (resolved, mtype, props)
        elif lname == "spine":
            ncx_id = child.attrib.get("toc")
            for itemref in child:
                if _local_name(itemref.tag) == "itemref":
                    idref = itemref.attrib.get("idref")
                    if idref:
                        spine_ids.append(idref)

    if not spine_ids:
        raise DocumentExtractionError(
            "Invalid EPUB archive: OPF spine contains no reading-order items."
        )

    # Build chapter title map from EPUB3 nav or EPUB2 NCX
    toc_titles: dict[str, str] = {}
    for href, _mtype, props in manifest.values():
        if "nav" in props.split() and href in member_set:
            nav_raw = read_zip_member_bounded(zf, href, 4 * 1024 * 1024)
            toc_titles.update(_parse_nav_toc(nav_raw, posixpath.dirname(href)))
            break
    if not toc_titles and ncx_id and ncx_id in manifest:
        ncx_href = manifest[ncx_id][0]
        if ncx_href in member_set:
            ncx_raw = read_zip_member_bounded(zf, ncx_href, 4 * 1024 * 1024)
            toc_titles.update(_parse_ncx_toc(ncx_raw, opf_dir))

    meta = {"title": title, "author": author, "language": language}

    def _iter() -> Iterator[ExtractedSection]:
        max_member = min(limits.max_binary_file_bytes, 32 * 1024 * 1024)
        for spine_idx, idref in enumerate(spine_ids, start=1):
            if cancel_token is not None and cancel_token.is_cancelled:
                raise IngestionCancelledError("EPUB extraction cancelled by user.")
            entry = manifest.get(idref)
            if entry is None:
                continue
            href, mtype, _props = entry
            if href not in member_set:
                continue
            if "html" not in mtype and not href.lower().endswith((".xhtml", ".html", ".htm")):
                continue

            raw_html = read_zip_member_bounded(zf, href, max_member)
            parser = _XHTMLTextExtractor()
            parser.feed(raw_html.decode("utf-8", errors="replace"))
            parsed_sections = parser.finish()
            chap_title = toc_titles.get(href) or (
                parsed_sections[0][0]
                if parsed_sections and parsed_sections[0][0]
                else f"Chapter {spine_idx}"
            )
            for sec_heading, body in parsed_sections:
                yield ExtractedSection(
                    chapter_title=chap_title,
                    section_title=sec_heading or chap_title,
                    text=body,
                )

    return meta, _iter()
