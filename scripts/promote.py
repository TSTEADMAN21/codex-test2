#!/usr/bin/env python3
"""Promote checked candidates from candidates_llm.md into canonical codex entity files.

Usage:
    python scripts/promote.py codex/sessions/<date>_<slug>/candidates_llm.md
    python scripts/promote.py codex/sessions/<date>_<slug>/candidates_llm.md --codex codex/
    python scripts/promote.py codex/sessions/<date>_<slug>/candidates_llm.md --dry-run

Checks every   - [x] **Name**   entry (NPCs/locations/items) and
               - [x] **Scene N:** Description   (plot threads).

Creates entity files if they don't exist, or appends appearance blocks if they do.
Nothing is written unless the box is checked.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


# ── slug helpers ──────────────────────────────────────────────────────────────

def slugify(name: str, max_len: int = 80) -> str:
    s = name.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:max_len].rstrip("-")


# ── markdown parser ────────────────────────────────────────────────────────────

ENTITY_SECTIONS = {
    "NPCs": "npcs",
    "Locations": "locations",
    "Items": "items",
}

CHECKED_ENTITY_RE   = re.compile(r"^- \[x\] \*\*(.+?)\*\*", re.IGNORECASE)
UNCHECKED_ENTITY_RE = re.compile(r"^- \[ \] \*\*(.+?)\*\*", re.IGNORECASE)
DESC_RE             = re.compile(r"^\s+- _(.+?)_\s*$")
EVIDENCE_RE         = re.compile(r"^\s+- evidence \(scene (\d+)\): \"(.+?)\"")

# Thread lines:  - [x] **Scene 3:** The party needs to ...
CHECKED_THREAD_RE   = re.compile(r"^- \[x\] \*\*Scene (\d+):\*\*\s+(.+)$", re.IGNORECASE)
UNCHECKED_THREAD_RE = re.compile(r"^- \[ \] \*\*Scene (\d+):\*\*\s+(.+)$", re.IGNORECASE)


def _parse_candidates(text: str) -> dict[str, list[dict]]:
    """Return {kind: [item_dict, ...]} for every CHECKED item."""
    results: dict[str, list[dict]] = {k: [] for k in ENTITY_SECTIONS.values()}
    results["threads"] = []

    current_kind: str | None = None
    current_entity: dict | None = None

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        hdr = line.strip().lstrip("#").strip()

        # ### NPCs / ### Locations / ### Items / ### Plot Threads Opened
        if line.startswith("###"):
            if current_entity and current_kind:
                results[current_kind].append(current_entity)
            current_entity = None
            if hdr in ENTITY_SECTIONS:
                current_kind = ENTITY_SECTIONS[hdr]
            elif hdr == "Plot Threads Opened":
                current_kind = "threads"
            else:
                current_kind = None
            i += 1
            continue

        # ## top-level section — leave thread/entity mode
        if line.startswith("## ") and not line.startswith("###"):
            if current_entity and current_kind:
                results[current_kind].append(current_entity)
            current_entity = None
            current_kind = None
            i += 1
            continue

        if current_kind is None:
            i += 1
            continue

        # ── Thread lines ─────────────────────────────────────────────────────
        if current_kind == "threads":
            m = CHECKED_THREAD_RE.match(line)
            if m:
                results["threads"].append({
                    "scene": int(m.group(1)),
                    "description": m.group(2).strip(),
                })
                i += 1
                continue
            # unchecked threads: just skip
            i += 1
            continue

        # ── Entity lines ──────────────────────────────────────────────────────
        m = CHECKED_ENTITY_RE.match(line)
        if m:
            if current_entity:
                results[current_kind].append(current_entity)
            current_entity = {"name": m.group(1).strip(), "description": "", "evidence": []}
            i += 1
            continue

        if UNCHECKED_ENTITY_RE.match(line):
            if current_entity:
                results[current_kind].append(current_entity)
            current_entity = None
            i += 1
            continue

        if current_entity:
            dm = DESC_RE.match(line)
            if dm:
                current_entity["description"] = dm.group(1)
                i += 1
                continue
            em = EVIDENCE_RE.match(line)
            if em:
                current_entity["evidence"].append({"scene": int(em.group(1)), "quote": em.group(2)})
                i += 1
                continue

        i += 1

    if current_entity and current_kind and current_kind != "threads":
        results[current_kind].append(current_entity)

    return results


# ── frontmatter helpers ────────────────────────────────────────────────────────

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _read_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, text[m.end():]


def _render_frontmatter(fm: dict) -> str:
    return "---\n" + "\n".join(f"{k}: {v}" for k, v in fm.items()) + "\n---\n"


def _session_slug_from_path(p: Path) -> str:
    return p.parent.name


def _session_date_from_slug(slug: str) -> str:
    return slug.split("_", 1)[0]


# ── entity file helpers ────────────────────────────────────────────────────────

def _appearance_block(session_date: str, session_slug: str, evidence: list[dict]) -> str:
    lines = [f"### {session_date} ({session_slug})\n"]
    for ev in evidence:
        lines.append(f'- Scene {ev["scene"]}: _{ev["quote"]}_')
    return "\n".join(lines) + "\n"


def _create_entity_file(path: Path, name: str, kind: str,
                        description: str, evidence: list[dict],
                        session_slug: str) -> None:
    session_date = _session_date_from_slug(session_slug)
    fm = {
        "name": name,
        "type": kind.rstrip("s"),
        "aliases": "[]",
        "first_seen": session_date,
        "sessions": f"[{session_slug}]",
        "tags": "[]",
        "status": "active",
    }
    body = f"\n## Description\n\n{description}\n\n## Appearances\n\n{_appearance_block(session_date, session_slug, evidence)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_frontmatter(fm) + body, encoding="utf-8")


def _append_entity_appearance(path: Path, evidence: list[dict], session_slug: str) -> None:
    text = path.read_text(encoding="utf-8")
    if session_slug in text:
        return
    fm, body = _read_frontmatter(text)
    sessions_val = fm.get("sessions", "[]")
    if session_slug not in sessions_val:
        cleaned = sessions_val.strip("[]")
        fm["sessions"] = f"[{cleaned}, {session_slug}]" if cleaned else f"[{session_slug}]"
    session_date = _session_date_from_slug(session_slug)
    new_body = body.rstrip("\n") + "\n\n" + _appearance_block(session_date, session_slug, evidence) + "\n"
    path.write_text(_render_frontmatter(fm) + new_body, encoding="utf-8")


# ── thread file helpers ────────────────────────────────────────────────────────

def _thread_appearance_block(session_date: str, session_slug: str, scene: int, description: str) -> str:
    return f"### {session_date} ({session_slug})\n\n- Scene {scene}: _{description}_\n"


def _create_thread_file(path: Path, description: str, scene: int, session_slug: str) -> None:
    session_date = _session_date_from_slug(session_slug)
    fm = {
        "name": description,
        "type": "thread",
        "status": "open",
        "opened": session_date,
        "opened_scene": str(scene),
        "resolved": "",
        "sessions": f"[{session_slug}]",
        "tags": "[]",
    }
    body = (
        f"\n## Description\n\n{description}\n\n"
        f"## Appearances\n\n{_thread_appearance_block(session_date, session_slug, scene, description)}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_frontmatter(fm) + body, encoding="utf-8")


def _append_thread_appearance(path: Path, description: str, scene: int, session_slug: str) -> None:
    text = path.read_text(encoding="utf-8")
    if session_slug in text:
        return
    fm, body = _read_frontmatter(text)
    sessions_val = fm.get("sessions", "[]")
    if session_slug not in sessions_val:
        cleaned = sessions_val.strip("[]")
        fm["sessions"] = f"[{cleaned}, {session_slug}]" if cleaned else f"[{session_slug}]"
    session_date = _session_date_from_slug(session_slug)
    new_body = body.rstrip("\n") + "\n\n" + _thread_appearance_block(session_date, session_slug, scene, description) + "\n"
    path.write_text(_render_frontmatter(fm) + new_body, encoding="utf-8")


# ── main promote logic ────────────────────────────────────────────────────────

KIND_DIRS = {
    "npcs": "npcs",
    "locations": "locations",
    "items": "items",
    "threads": "plot-threads",
}


def promote(candidates_path: Path, codex_root: Path, dry_run: bool = False) -> None:
    text = candidates_path.read_text(encoding="utf-8")
    parsed = _parse_candidates(text)
    session_slug = _session_slug_from_path(candidates_path)

    created: list[str] = []
    updated: list[str] = []

    for kind, items in parsed.items():
        if not items:
            continue
        kind_dir = codex_root / KIND_DIRS[kind]

        for item in items:
            if kind == "threads":
                desc = item["description"]
                slug = slugify(desc, max_len=60)
                target = kind_dir / f"{slug}.md"
                label = f"plot-threads/{slug}.md"

                if dry_run:
                    action = "CREATE" if not target.exists() else "UPDATE"
                    print(f"  [{action}] {label}  ← {desc[:60]!r}")
                    continue

                if not target.exists():
                    _create_thread_file(target, desc, item["scene"], session_slug)
                    created.append(label)
                else:
                    _append_thread_appearance(target, desc, item["scene"], session_slug)
                    updated.append(label)
            else:
                name = item["name"]
                slug = slugify(name)
                target = kind_dir / f"{slug}.md"
                label = f"{kind}/{slug}.md"

                if dry_run:
                    action = "CREATE" if not target.exists() else "UPDATE"
                    print(f"  [{action}] {label}  ← {name!r}")
                    continue

                if not target.exists():
                    _create_entity_file(target, name, kind, item["description"], item["evidence"], session_slug)
                    created.append(label)
                else:
                    _append_entity_appearance(target, item["evidence"], session_slug)
                    updated.append(label)

    if dry_run:
        return

    total = len(created) + len(updated)
    print(f"\nPromote complete — {total} items processed.")
    if created:
        print(f"  Created ({len(created)}):")
        for f in created:
            print(f"    {f}")
    if updated:
        print(f"  Appended ({len(updated)}):")
        for f in updated:
            print(f"    {f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("candidates", type=Path,
                        help="Path to candidates_llm.md to promote from")
    parser.add_argument("--codex", type=Path, default=Path("codex"),
                        help="Codex root directory (default: codex/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be created/updated without writing files")
    args = parser.parse_args()

    if not args.candidates.exists():
        sys.exit(f"error: {args.candidates} not found")
    if not args.codex.exists():
        sys.exit(f"error: codex root {args.codex} not found")

    promote(args.candidates, args.codex, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
