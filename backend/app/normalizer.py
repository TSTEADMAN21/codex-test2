"""Clean up common encoding artifacts in raw session notes."""
from __future__ import annotations
import re
import unicodedata

MOJIBAKE_MAP = {
    "â\x80\x99": "'",
    "â\x80\x98": "'",
    "â\x80\x9c": '"',
    "â\x80\x9d": '"',
    "â\x80\x93": "-",
    "â\x80\x94": "-",
    "â\x80\xa6": "...",
    "\xe2\x80\x99": "'",
    "\xe2\x80\x9c": '"',
    "\xe2\x80\x9d": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2026": "...",
    "â": "'",
}

REAL_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b")
IN_GAME_DATE_RE = re.compile(
    r"(?i)(\d+(?:st|nd|rd|th)?\s+(?:week|day)\s+of\s+\w+(?:\s*\(the?\s*\d+(?:st|nd|rd|th)?\))?[^\n]{0,60})"
)


def normalize_text(text: str) -> str:
    for bad, good in MOJIBAKE_MAP.items():
        text = text.replace(bad, good)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def detect_real_date(text: str) -> str | None:
    m = REAL_DATE_RE.search(text)
    if not m:
        return None
    mo, dy, yr = m.group(1), m.group(2), m.group(3)
    if len(yr) == 2:
        yr = "20" + yr
    return f"{int(yr):04d}-{int(mo):02d}-{int(dy):02d}"


def detect_in_game_date(text: str) -> str | None:
    m = IN_GAME_DATE_RE.search(text)
    return m.group(1).strip() if m else None
