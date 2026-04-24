"""Turn a raw session notes file into canonical markdown in codex/."""
from __future__ import annotations
import asyncio
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from . import candidates, extractors, normalizer, scene_splitter


@dataclass
class IngestResult:
    session_path: Path
    candidates_path: Path
    real_date: Optional[str]
    in_game_date: Optional[str]
    scene_count: int
    candidate_counts: dict
    llm_report: Optional[extractors.ExtractionReport] = None


def _slug(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:60] or "session"


def _session_filename(real_date: str | None, title_hint: str) -> str:
    d = real_date or date.today().isoformat()
    return f"{d}_{_slug(title_hint)}.md"


def ingest_file(raw_path: Path, codex_root: Path, notetaker: str = "unknown",
                use_llm: bool = False, llm_model: Optional[str] = None,
                prompts_dir: Optional[Path] = None) -> IngestResult:
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

    llm_report: Optional[extractors.ExtractionReport] = None
    if use_llm:
        prompts_dir = prompts_dir or (Path(__file__).parent / "prompts")

        def progress(i, total, title):
            print(f"  [llm] scene {i}/{total}: {title[:60]}")

        llm_report = asyncio.run(
            extractors.extract_session(scenes, prompts_dir, model=llm_model, progress=progress)
        )
        (session_dir / "candidates_llm.md").write_text(
            _render_llm_candidates(llm_report), encoding="utf-8"
        )

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
        llm_report=llm_report,
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


def _render_llm_candidates(report: extractors.ExtractionReport) -> str:
    from collections import defaultdict

    buckets = defaultdict(dict)  # kind -> {name: [ (scene_idx, description, quote) ]}
    for scene, ext in report.scene_extractions:
        for npc in ext.npcs:
            buckets["npcs"].setdefault(npc.name, []).append(
                (scene.index, npc.description, npc.evidence_quote))
        for loc in ext.locations:
            buckets["locations"].setdefault(loc.name, []).append(
                (scene.index, loc.description, loc.evidence_quote))
        for it in ext.items:
            buckets["items"].setdefault(it.name, []).append(
                (scene.index, it.description, it.evidence_quote))

    def render_bucket(title: str, kind: str) -> str:
        items = buckets.get(kind, {})
        if not items:
            return f"### {title}\n\n_None extracted._\n"
        lines = [f"### {title}\n"]
        for name, sightings in sorted(items.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"- [ ] **{name}** ({len(sightings)} scene{'s' if len(sightings) > 1 else ''})")
            first = sightings[0]
            lines.append(f"    - _{first[1]}_")
            lines.append(f"    - evidence (scene {first[0]}): \"{first[2][:140]}{'...' if len(first[2]) > 140 else ''}\"")
        return "\n".join(lines) + "\n"

    events_block = ""
    all_events = [(s.index, e) for s, ext in report.scene_extractions for e in ext.events]
    if all_events:
        events_block = "### Events\n\n" + "\n".join(
            f"- [ ] **Scene {idx}:** {e.summary}\n    - evidence: \"{e.evidence_quote[:140]}\""
            for idx, e in all_events
        ) + "\n"

    threads_block = ""
    all_threads = [(s.index, t) for s, ext in report.scene_extractions for t in ext.plot_threads_opened]
    if all_threads:
        threads_block = "### Plot Threads Opened\n\n" + "\n".join(
            f"- [ ] **Scene {idx}:** {t.thread}"
            for idx, t in all_threads
        ) + "\n"

    disguise_block = ""
    all_disguise = [(s.index, d) for s, ext in report.scene_extractions for d in ext.disguise_alerts]
    if all_disguise:
        disguise_block = (
            "## Disguise / pseudonym alerts\n\n"
            "**DO NOT** promote these as aliases.\n\n"
            + "\n".join(
                f"- Scene {idx}: \"{d.fake_name}\" used by {d.real_identity} — evidence: \"{d.evidence_quote[:140]}\""
                for idx, d in all_disguise
            ) + "\n"
        )

    diagnostics = ""
    if report.dropped_hallucinations or report.parse_failures:
        diagnostics = "\n## Diagnostics\n\n"
        if report.dropped_hallucinations:
            diagnostics += f"- Dropped {len(report.dropped_hallucinations)} hallucination(s) (evidence quote not found in scene)\n"
        if report.parse_failures:
            diagnostics += f"- {len(report.parse_failures)} scene(s) failed to parse\n"
            for f in report.parse_failures[:3]:
                diagnostics += f"    - scene {f['scene_index']} ({f['title'][:40]}): {f['error'][:120]}\n"

    return (
        "---\nstatus: needs_review\nextractor: llm\n---\n\n"
        "# LLM Entity Candidates\n\n"
        "Each entity below was extracted by the local LLM and VERIFIED against the scene text "
        "(hallucinated entities without valid evidence quotes have been dropped). "
        "Check each box you want promoted into the canonical codex.\n\n"
        f"{render_bucket('NPCs', 'npcs')}\n"
        f"{render_bucket('Locations', 'locations')}\n"
        f"{render_bucket('Items', 'items')}\n"
        f"{events_block}\n"
        f"{threads_block}\n"
        f"{disguise_block}"
        f"{diagnostics}"
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
