"""Parse a canonical entity markdown file into a structured dict for templates."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

import frontmatter


def _parse_list_field(val) -> list[str]:
    """Turn '[a, b, c]', '[]', or an already-parsed list into a Python list of strings."""
    if isinstance(val, list):
        return [str(v).strip() for v in val if str(v).strip()]
    s = str(val).strip().strip("[]")
    if not s:
        return []
    return [v.strip().strip("'\"") for v in s.split(",") if v.strip()]


def _parse_appearances(body: str) -> list[dict]:
    """Parse the ## Appearances section into structured data.

    Each ### header starts a new appearance block.
    Lines like '- Scene N: _quote_' are scene entries.
    """
    appearances: list[dict] = []
    current: Optional[dict] = None

    in_appearances = False
    for line in body.splitlines():
        if line.startswith("## Appearances"):
            in_appearances = True
            continue
        if line.startswith("## ") and in_appearances:
            # Hit a different top-level section — stop
            break
        if not in_appearances:
            continue

        # ### 2019-11-26 (session-slug)
        if line.startswith("### "):
            if current:
                appearances.append(current)
            header = line[4:].strip()
            # Extract date and slug from "2019-11-26 (slug)" or just "2019-11-26"
            m = re.match(r"(\d{4}-\d{2}-\d{2})\s*(?:\((.+?)\))?", header)
            if m:
                current = {
                    "date": m.group(1),
                    "session_slug": m.group(2) or "",
                    "scenes": [],
                }
            else:
                current = {"date": header, "session_slug": "", "scenes": []}
            continue

        # - Scene N: _quote_
        if current and line.strip().startswith("- Scene"):
            m2 = re.match(r"\s*- Scene (\d+):\s*_(.+?)_\s*$", line)
            if m2:
                current["scenes"].append({
                    "num": int(m2.group(1)),
                    "quote": m2.group(2),
                })

    if current:
        appearances.append(current)
    return appearances


def _parse_description(body: str) -> str:
    in_desc = False
    lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("## Description"):
            in_desc = True
            continue
        if line.startswith("## ") and in_desc:
            break
        if in_desc:
            lines.append(line)
    return "\n".join(lines).strip()


def _parse_personal_storylines(body: str) -> list[str]:
    """Parse bullet lines from ## Personal Storylines section."""
    lines: list[str] = []
    in_section = False
    for line in body.splitlines():
        if line.startswith("## Personal Storylines"):
            in_section = True
            continue
        if line.startswith("## ") and in_section:
            break
        if in_section and line.strip().startswith("- "):
            lines.append(line.strip()[2:].strip())
    return lines


def load(path: Path) -> dict:
    """Return a template-ready dict for an entity file."""
    fm = frontmatter.load(path)

    name = str(fm.get("name", path.stem.replace("-", " ").title()))
    kind = str(fm.get("type", "unknown"))
    aliases = _parse_list_field(fm.get("aliases", []))
    tags = _parse_list_field(fm.get("tags", []))
    sessions = _parse_list_field(fm.get("sessions", []))
    first_seen = str(fm.get("first_seen", ""))
    status = str(fm.get("status", "active"))
    role = str(fm.get("role") or "")

    description = _parse_description(fm.content)
    appearances = _parse_appearances(fm.content)
    personal_storylines = _parse_personal_storylines(fm.content)

    return {
        "name": name,
        "kind": kind,
        "role": role,
        "aliases": aliases,
        "tags": tags,
        "sessions": sessions,
        "first_seen": first_seen,
        "status": status,
        "description": description,
        "personal_storylines": personal_storylines,
        "appearances": appearances,
    }
