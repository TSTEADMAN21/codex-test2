#!/usr/bin/env python3
"""CLI wrapper for the ingestion pipeline.

Usage:
    python scripts/ingest.py raw-notes/testing.txt --notetaker travis
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ingest import ingest_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a raw session notes file.")
    parser.add_argument("raw", type=Path, help="Path to the raw session notes file")
    parser.add_argument("--notetaker", default="travis",
                        help="Name of the player whose notes these are (e.g. travis, sarah)")
    parser.add_argument("--codex", type=Path, default=ROOT / "codex",
                        help="Path to the codex root directory")
    parser.add_argument("--use-llm", action="store_true",
                        help="Run LLM-based entity extraction (requires Ollama running). "
                             "Slower (~2-5s per scene) but much higher quality than heuristic.")
    parser.add_argument("--llm-model", default=None,
                        help="Ollama model name (default: $OLLAMA_MODEL or llama3.1:8b)")
    args = parser.parse_args()

    if not args.raw.exists():
        print(f"error: raw file not found: {args.raw}", file=sys.stderr)
        return 1

    result = ingest_file(args.raw, args.codex, notetaker=args.notetaker,
                        use_llm=args.use_llm, llm_model=args.llm_model)
    print(f"Session directory : {result.session_path.relative_to(ROOT)}")
    print(f"Real-world date    : {result.real_date or 'not detected'}")
    print(f"In-game date       : {result.in_game_date or 'not detected'}")
    print(f"Scenes split       : {result.scene_count}")
    print(f"Heuristic candidates: {result.candidate_counts}")
    print(f"Review (heuristic) : {result.candidates_path.relative_to(ROOT)}")
    if result.llm_report is not None:
        r = result.llm_report
        npcs = sum(len(e.npcs) for _, e in r.scene_extractions)
        locs = sum(len(e.locations) for _, e in r.scene_extractions)
        items = sum(len(e.items) for _, e in r.scene_extractions)
        print(f"LLM extracted      : {npcs} NPC mentions, {locs} location mentions, {items} item mentions")
        print(f"Hallucinations dropped: {len(r.dropped_hallucinations)}")
        print(f"Scene parse failures : {len(r.parse_failures)}")
        print(f"Review (LLM)       : {result.session_path.relative_to(ROOT)}/candidates_llm.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
