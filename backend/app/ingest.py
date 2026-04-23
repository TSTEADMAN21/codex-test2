"""Turn a raw session notes file into canonical markdown in codex/."""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import candidates, normalizer, scene_splitter


@dataclass
class IngestResult:
    session_path: Path
    candidates_path: Path
    real_date: str | None
    in_game_date: str | None
    scene_count: int
    candidate_counts: dict[str, int]


def _slug(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:60] or "session"


def _session_filename(real_date: str | None, title_hint: str) -> str:
    d = real_date or date.today().isoformat()
    return f"{d}_{_slug(title_hint)}.md"


def ingest_file(raw_path: Path, codex_root: Path, notetaker: str = "unknown") -> IngestResult:
    raw = raw_path.read_text(encoding="utf-8", errors="replace")
    clean = normalizer.normalize_text(raw)

    real_date = normalizer.detect_real_date(clean)
    in_game_date = normalizer.detect_in_game_date(clean)
    scenes = scene_splitter.split_into_scenes(clean)
    cand = candidates.extract_candidates(clean)

    title_hint = scenes[0].title if scenes else raw_path.stem
    session_dir = codex_root / "sessions" / _session_filename(real_date, title_hint).removesuffix(".md")
    session_dir.mkdir(parents=True, exist_ok=True)

    notes_path = session_dir / f"notes-{notetaker}.md"
    notes_path.write_text(_render_notes(clean, raw_path, notetaker, real_date, in_game_date, scenes), encoding="utf-8")

    cand_path = session_dir / "candidates.md"
    cand_path.write_text(_render_candidates(cand), encoding="utf-8")

    merged_path = session_dir / "merged.md"
    if not merged_path.exists():
        merged_path.write_text(_render_merged_stub(real_date, in_game_date, scenes), encoding="utf-8")

    return IngestResult(
        session_path=session_dir,
        candidates_path=cand_path,
        real_date=real_date,
        in_game_date=in_game_date,
        scene_count=len(scenes),
        candidate_counts={
            "npcs": len(cand.npcs),
            "locations": len(cand.locations),
            "items": len(cand.items),
            "disguise_flags": len(cand.disguise_flags),
        },
    )


def _render_notes(clean: str, raw_path: Path, notetaker: str,
                  real_date: str | None, in_game_date: str | None,
                  scenes: list[scene_splitter.Scene]) -> str:
    frontmatter = [
        "---",
        f"source_file: {raw_path.name}",
        f"notetaker: {notetaker}",
        f"real_date: {real_date or 'unknown'}",
        f"in_game_date: {in_game_date or 'unknown'}",
        f"scene_count: {len(scenes)}",
        "note_detail: raw",
        "---",
        "",
    ]
    lines = ["\n".join(frontmatter)]
    for scene in scenes:
        lines.append(f"## Scene {scene.index}: {scene.title}\n")
        lines.append(scene.text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _render_merged_stub(real_date: str | None, in_game_date: str | None,
                        scenes: list[scene_splitter.Scene]) -> str:
    return (
        "---\n"
        f"real_date: {real_date or 'unknown'}\n"
        f"in_game_date: {in_game_date or 'unknown'}\n"
        "status: stub\n"
        "---\n\n"
        "# Merged Session Log (stub)\n\n"
        "This file will hold the consolidated canonical log after multiple notetakers' inputs "
        "are reconciled. Dialogue should be preserved verbatim. Conflicts belong in "
        "`conflicts.md`.\n\n"
        f"Detected {len(scenes)} scene(s) in the raw notes — review `notes-*.md` and copy scene-by-scene.\n"
    )


def _render_candidates(cand: candidates.CandidateSet) -> str:
    def table(title: str, items: dict[str, int]) -> str:
        if not items:
            return f"### {title}\n\n_None detected._\n"
        rows = [f"- [ ] **{name}** ({count} mention{'s' if count > 1 else ''})"
                for name, count in sorted(items.items(), key=lambda kv: -kv[1])]
        return f"### {title}\n\n" + "\n".join(rows) + "\n"

    flags = ""
    if cand.disguise_flags:
        bullets = "\n".join(f"- {f}" for f in cand.disguise_flags)
        flags = (
            "## Disguise / pseudonym flags\n\n"
            "The text contains phrases suggesting a fake name or disguise. "
            "**Do NOT** promote these as aliases without review.\n\n"
            f"{bullets}\n"
        )

    return (
        "---\nstatus: needs_review\n---\n\n"
        "# Entity Candidates\n\n"
        "Check each item that should become a canonical entry. "
        "Unchecked items will be ignored on the next pass. "
        "**Nothing here has been added to the codex yet.**\n\n"
        f"{table('NPCs', cand.npcs)}\n"
        f"{table('Locations', cand.locations)}\n"
        f"{table('Items', cand.items)}\n"
        f"{flags}"
    )
