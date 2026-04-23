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
    args = parser.parse_args()

    if not args.raw.exists():
        print(f"error: raw file not found: {args.raw}", file=sys.stderr)
        return 1

    result = ingest_file(args.raw, args.codex, notetaker=args.notetaker)
    print(f"Session directory : {result.session_path.relative_to(ROOT)}")
    print(f"Real-world date    : {result.real_date or 'not detected'}")
    print(f"In-game date       : {result.in_game_date or 'not detected'}")
    print(f"Scenes split       : {result.scene_count}")
    print(f"Candidates found   : {result.candidate_counts}")
    print(f"Review candidates  : {result.candidates_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
