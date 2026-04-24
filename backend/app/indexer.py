"""Walk the codex/ tree and load every .md file into SQLite FTS."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from . import db

KIND_BY_DIR = {
    "sessions": "session",
    "npcs": "npc",
    "locations": "location",
    "events": "event",
    "items": "item",
    "factions": "faction",
    "party": "party",
    "plot-threads": "thread",
}


def reindex(codex_root: Path, db_path: Path) -> int:
    conn = db.connect(db_path)
    count = 0
    for md in codex_root.rglob("*.md"):
        rel = md.relative_to(codex_root)
        kind = KIND_BY_DIR.get(rel.parts[0], "other")
        try:
            fm = frontmatter.load(md)
            body = fm.content
            title = fm.get("name") or md.stem.replace("-", " ").replace("_", " ").title()
        except Exception:
            body = md.read_text(encoding="utf-8", errors="replace")
            title = md.stem
        updated = datetime.now(timezone.utc).isoformat()
        db.upsert_document(conn, path=str(rel), kind=kind,
                           title=str(title), body=body, updated_at=updated)
        count += 1
    conn.close()
    return count
