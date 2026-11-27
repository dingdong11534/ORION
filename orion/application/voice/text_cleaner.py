"""Speech text normalization: strip Markdown, URLs, and citation markers."""

import re

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_CITATION_RE = re.compile(r"\[\s*(?:S\d+|\d+)\s*\]")
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MD_SYMBOLS_RE = re.compile(r"[*_~#>]+")


def clean_text_for_speech(text: str) -> str:
    """Return speakable plain text with Markdown, URLs, and citation markers removed."""
    s = _CODE_BLOCK_RE.sub(" code block omitted ", text or "")
    s = _MD_LINK_RE.sub(r"\1", s)
    s = _URL_RE.sub("", s)
    s = _CITATION_RE.sub("", s)
    s = _INLINE_CODE_RE.sub(r"\1", s)
    s = _MD_SYMBOLS_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def split_sentences_for_tts(text: str) -> list[str]:
    """Split cleaned text into short sentences so first audio starts quickly."""
    cleaned = clean_text_for_speech(text)
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return [p.strip() for p in parts if p and p.strip()]
