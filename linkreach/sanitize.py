"""Clean scraped text before it goes anywhere near a prompt."""

from __future__ import annotations

import html
import re
import unicodedata

FIELD_CAP = 500

_TAG_RE = re.compile(r"<[^>]*>")
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
# zero-width and bidi-control characters
_INVISIBLE = {
    chr(c)
    for c in (
        0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064,
        0xFEFF, 0x180E, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068,
        0x2069, 0x00AD,
    )
}
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


def strip_html(text: str) -> str:
    text = _SCRIPT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return html.unescape(text)


def strip_invisible(text: str) -> str:
    out = []
    for ch in text:
        if ch in _INVISIBLE:
            continue
        cat = unicodedata.category(ch)
        if ch == "\n":
            out.append(ch)
        elif cat in ("Cc", "Cf", "Cs", "Co", "Cn"):
            # control, format, surrogate, private-use, unassigned
            out.append(" " if ch in "\t\r\x0b\x0c" else "")
        else:
            out.append(ch)
    return "".join(out)


def clean(text: str | None, cap: int = FIELD_CAP) -> str:
    """HTML stripped, control/zero-width chars removed, whitespace collapsed, capped."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = strip_html(text)
    text = strip_invisible(text)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _NL_RE.sub("\n\n", text).strip()
    if len(text) > cap:
        text = text[:cap].rstrip()
    return text


def clean_line(text: str | None, cap: int = 200) -> str:
    """Single-line variant for names and titles."""
    return clean(text, cap).replace("\n", " ").strip()


def first_name(full_name: str) -> str:
    name = clean_line(full_name, 80)
    # drop credentials like "Jane Doe, PMP" and emoji/symbols
    name = name.split(",")[0]
    parts = [p for p in re.split(r"\s+", name) if p]
    for p in parts:
        token = re.sub(r"[^\w'\-]", "", p)
        if token and token.lower() not in {"dr", "mr", "mrs", "ms", "mx", "prof"}:
            return token
    return parts[0] if parts else ""
