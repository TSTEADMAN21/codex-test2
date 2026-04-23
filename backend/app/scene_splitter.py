"""Split prose-style session notes into scenes using heuristics.

The DM-log style in this project has no explicit scene breaks. We detect
shifts using:
  - paragraph breaks combined with location/POV cues
  - initiative / combat markers ("init", "surprise round")
  - hard cues like 'heads to', 'makes her way to', 'arrive at'
  - explicit date stamps

Output is a list of {title, start, end, text} chunks preserving the original
wording. No summarization — splitting only.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

LOCATION_SHIFT_RE = re.compile(
    r"(?im)^(?:\s*)(?:"
    r"(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:heads?|goes|walks|arrives?|makes?\s+(?:his|her|their)\s+way)"
    r"|We\s+(?:arrive|head|make\s+our\s+way|are)"
    r"|(?:At|In|Inside|Outside)\s+[A-Z]"
    r"|As\s+(?:dark|night|dawn|morning)"
    r"|Party\s+(?:meets|heads|walks)"
    r"|The\s+party\s+"
    r")"
)

COMBAT_MARKER_RE = re.compile(r"(?im)^\s*(init|initiative|surprise round|combat)\b")
SECTION_BREAK_HINTS = [
    "init",
    "initiative",
    "surprise round",
]


@dataclass
class Scene:
    index: int
    title: str
    text: str


def _make_title(paragraph: str) -> str:
    first = paragraph.strip().split("\n", 1)[0]
    words = first.split()
    title = " ".join(words[:10])
    title = re.sub(r"[^\w\s\-]", "", title).strip()
    return title[:80] if title else "Untitled Scene"


def split_into_scenes(text: str) -> list[Scene]:
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    scenes: list[list[str]] = [[paragraphs[0]]]
    for para in paragraphs[1:]:
        starts_scene = bool(LOCATION_SHIFT_RE.match(para)) or bool(COMBAT_MARKER_RE.match(para))
        if starts_scene and len(" ".join(scenes[-1])) > 400:
            scenes.append([para])
        else:
            scenes[-1].append(para)

    out: list[Scene] = []
    for i, chunk in enumerate(scenes, start=1):
        body = "\n\n".join(chunk).strip()
        out.append(Scene(index=i, title=_make_title(body), text=body))
    return out
