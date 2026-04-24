"""FastAPI entrypoint for the adventure-codex backend."""
from __future__ import annotations
import os
from pathlib import Path

import re as _re

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "it", "in", "on", "at", "to", "for", "of", "and", "or",
    "but", "not", "with", "this", "that", "was", "are", "be", "by", "as", "we", "do",
    "did", "have", "has", "had", "what", "who", "how", "when", "where", "why", "i",
    "me", "my", "our", "you", "your", "he", "she", "they", "his", "her", "their",
    "can", "will", "would", "could", "should", "about", "from", "up", "than", "so",
    "know", "tell", "find", "get", "any", "if", "then", "there", "here", "which",
    "been", "into", "more", "also", "just", "does", "over", "after", "before",
})


def _question_to_fts(question: str) -> str:
    """Convert a natural-language question into an FTS5 OR query of content words."""
    words = _re.findall(r"[a-zA-Z']+", question)
    terms = [w for w in words if len(w) > 2 and w.lower() not in _STOP_WORDS]
    if not terms:
        # Fallback: anything longer than 2 chars
        terms = [w for w in words if len(w) > 2]
    if not terms:
        return question
    # Quote each term to avoid FTS5 syntax errors, join with OR
    return " OR ".join(f'"{t}"' for t in terms)

import json as _json
import edge_tts

from fastapi import FastAPI, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import frontmatter as _fm

from . import db, entity_reader, extractors as _extractors, indexer, ollama_client, scene_splitter as _scene_splitter

ROOT = Path(__file__).resolve().parents[2]
CODEX_ROOT = Path(os.environ.get("CODEX_ROOT", ROOT / "codex"))
DB_PATH = Path(os.environ.get("CODEX_DB", ROOT / "data" / "codex.db"))
FRONTEND_DIR = ROOT / "frontend"
SYSTEM_PROMPT = (ROOT / "backend" / "app" / "prompts" / "system_constraints.md").read_text(encoding="utf-8")

app = FastAPI(title="Adventure Codex")
templates = Jinja2Templates(directory=str(FRONTEND_DIR))
if (FRONTEND_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")


def _db():
    return db.connect(DB_PATH)


# ── Session helpers ───────────────────────────────────────────────────────────

def _load_session(session_dir: Path) -> dict:
    """Return metadata dict for one session directory."""
    notes_files = sorted(session_dir.glob("notes-*.md"))
    notes_path = notes_files[0] if notes_files else None

    real_date = in_game_date = notetaker = ""
    scene_count = 0
    notes_body = ""

    if notes_path:
        try:
            fm = _fm.load(notes_path)
            real_date    = str(fm.get("real_date", ""))
            in_game_date = str(fm.get("in_game_date", ""))
            notetaker    = str(fm.get("notetaker", ""))
            scene_count  = int(fm.get("scene_count", 0))
            notes_body   = fm.content
        except Exception:
            pass

    summary_path = session_dir / "summary.md"
    summary_text = ""
    if summary_path.exists():
        try:
            sf = _fm.load(summary_path)
            summary_text = sf.content.strip()
        except Exception:
            summary_text = summary_path.read_text(encoding="utf-8").strip()

    slug = session_dir.name
    year = real_date[:4] if real_date else slug[:4]

    has_narration       = (session_dir / "narration.mp3").exists()
    has_notes_narration = (session_dir / "narration-notes.mp3").exists()

    return {
        "slug": slug,
        "path": f"sessions/{slug}",
        "real_date": real_date,
        "in_game_date": in_game_date,
        "notetaker": notetaker,
        "scene_count": scene_count,
        "year": year,
        "has_summary": bool(summary_text),
        "summary_excerpt": summary_text[:280] + ("…" if len(summary_text) > 280 else ""),
        "summary": summary_text,
        "notes_body": notes_body,
        "notes_path": str(notes_path.relative_to(CODEX_ROOT)) if notes_path else "",
        "has_narration": has_narration,
        "has_notes_narration": has_notes_narration,
        "has_candidates": (session_dir / "candidates.json").exists(),
    }


def _all_sessions() -> list[dict]:
    sessions_dir = CODEX_ROOT / "sessions"
    if not sessions_dir.exists():
        return []
    sessions = []
    for d in sorted(sessions_dir.iterdir()):
        if d.is_dir():
            sessions.append(_load_session(d))
    sessions.sort(key=lambda s: s["real_date"], reverse=True)
    return sessions


def _sessions_by_year(sessions: list[dict]) -> list[tuple[str, list[dict]]]:
    """Return [(year, [session, ...]), ...] sorted newest year first."""
    from collections import defaultdict
    grouped: dict[str, list] = defaultdict(list)
    for s in sessions:
        grouped[s["year"]].append(s)
    return sorted(grouped.items(), key=lambda x: x[0], reverse=True)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    conn = _db()
    counts = {kind: len(db.list_documents(conn, kind))
              for kind in ("session", "npc", "location", "item", "thread", "party")}
    conn.close()
    reachable = await ollama_client.is_reachable()
    return templates.TemplateResponse(request, "index.html", {
        "counts": counts,
        "ollama_ok": reachable,
        "ollama_model": ollama_client.OLLAMA_MODEL,
    })


@app.get("/api/search")
async def api_search(q: str = Query(..., min_length=1), limit: int = 25):
    conn = _db()
    try:
        return {"results": db.search(conn, q, limit=limit)}
    finally:
        conn.close()


@app.get("/search", response_class=HTMLResponse)
async def html_search(request: Request, q: str = Query("", min_length=0), limit: int = 25):
    results: list[dict] = []
    if q.strip():
        conn = _db()
        try:
            results = db.search(conn, q, limit=limit)
        finally:
            conn.close()
    return templates.TemplateResponse(request, "_search_results.html", {
        "q": q, "results": results,
    })


@app.get("/api/documents")
async def api_documents(kind: str | None = None):
    conn = _db()
    try:
        return {"documents": db.list_documents(conn, kind)}
    finally:
        conn.close()


_KIND_DIRS = {"npc": "npcs", "location": "locations", "item": "items", "thread": "plot-threads", "party": "party"}


def _load_entities_of_kind(kind: str) -> list[dict]:
    dir_name = _KIND_DIRS.get(kind, kind + "s")
    kind_dir = CODEX_ROOT / dir_name
    if not kind_dir.exists():
        return []
    entities = []
    for md in sorted(kind_dir.glob("*.md")):
        try:
            data = entity_reader.load(md)
            data["path"] = f"{dir_name}/{md.name}"
            entities.append(data)
        except Exception:
            continue
    return sorted(entities, key=lambda e: e.get("name", "").lower())


@app.get("/browse", response_class=HTMLResponse)
async def browse(request: Request, kind: str = "npc"):
    entities = _load_entities_of_kind(kind)
    return templates.TemplateResponse(request, "browse.html", {
        "entities": entities,
        "kind": kind,
    })


@app.get("/browse/grid", response_class=HTMLResponse)
async def browse_grid(request: Request, kind: str = "npc"):
    entities = _load_entities_of_kind(kind)
    return templates.TemplateResponse(request, "_browse_grid.html", {
        "entities": entities,
        "kind": kind,
    })


@app.get("/entity/partial", response_class=HTMLResponse)
async def entity_partial(request: Request, path: str):
    full = (CODEX_ROOT / path).resolve()
    if not str(full).startswith(str(CODEX_ROOT.resolve())) or not full.exists():
        raise HTTPException(404, "entity not found")
    try:
        data = entity_reader.load(full)
    except Exception as exc:
        raise HTTPException(500, f"could not parse entity: {exc}")
    return templates.TemplateResponse(request, "_entity_partial.html", {
        "path": path,
        **data,
    })


@app.get("/entity", response_class=HTMLResponse)
async def entity_page(request: Request, path: str):
    full = (CODEX_ROOT / path).resolve()
    if not str(full).startswith(str(CODEX_ROOT.resolve())) or not full.exists():
        raise HTTPException(404, "entity not found")
    try:
        data = entity_reader.load(full)
    except Exception as exc:
        raise HTTPException(500, f"could not parse entity: {exc}")
    return templates.TemplateResponse(request, "_entity.html", {
        "path": path,
        **data,
    })


@app.get("/api/document")
async def api_document(path: str):
    full = (CODEX_ROOT / path).resolve()
    if not str(full).startswith(str(CODEX_ROOT.resolve())) or not full.exists():
        raise HTTPException(404, "not found")
    return {"path": path, "body": full.read_text(encoding="utf-8")}


@app.put("/api/document")
async def api_document_save(payload: dict):
    path = (payload.get("path") or "").strip()
    content = payload.get("content", "")
    if not path:
        raise HTTPException(400, "path required")
    full = (CODEX_ROOT / path).resolve()
    if not str(full).startswith(str(CODEX_ROOT.resolve())):
        raise HTTPException(403, "path outside codex")
    if not full.exists():
        raise HTTPException(404, "not found")
    full.write_text(content, encoding="utf-8")
    indexer.reindex(CODEX_ROOT, DB_PATH)
    return {"path": path, "saved": True}


@app.get("/sessions", response_class=HTMLResponse)
async def sessions_index(request: Request):
    sessions = _all_sessions()
    by_year = _sessions_by_year(sessions)
    return templates.TemplateResponse(request, "sessions.html", {
        "by_year": by_year,
        "total": len(sessions),
    })


@app.get("/session", response_class=HTMLResponse)
async def session_detail(request: Request, path: str):
    full = (CODEX_ROOT / path).resolve()
    if not str(full).startswith(str(CODEX_ROOT.resolve())) or not full.is_dir():
        raise HTTPException(404, "session not found")
    data = _load_session(full)
    reachable = await ollama_client.is_reachable()
    return templates.TemplateResponse(request, "_session.html", {
        "ollama_ok": reachable,
        **data,
    })


@app.post("/api/session/summarize")
async def summarize_session(payload: dict):
    path = (payload.get("path") or "").strip()
    full = (CODEX_ROOT / path).resolve()
    if not str(full).startswith(str(CODEX_ROOT.resolve())) or not full.is_dir():
        raise HTTPException(404, "session not found")
    if not await ollama_client.is_reachable():
        raise HTTPException(503, "Ollama not reachable")

    data = _load_session(full)
    if not data["notes_body"]:
        raise HTTPException(400, "no session notes found to summarize")

    prompt = (
        "Write a 2-3 paragraph narrative session recap in past tense, as if briefing a player who missed the session. "
        "Cover: what the party investigated or accomplished, key NPCs they met, any combat or significant skill checks, "
        "and which plot threads advanced or opened. Use the character names exactly as written. "
        "Do not invent any details not present in the notes. Do not include stat blocks or canonical lore.\n\n"
        f"REAL DATE: {data['real_date']}\n"
        f"IN-GAME DATE: {data['in_game_date']}\n\n"
        f"SESSION NOTES:\n{data['notes_body']}"
    )

    summary_path = full / "summary.md"
    chunks: list[str] = []

    async def stream_and_save():
        async for chunk in ollama_client.stream(prompt, system=SYSTEM_PROMPT):
            chunks.append(chunk)
            yield chunk
        summary_text = "".join(chunks)
        fm_header = (
            f"---\ngenerated: {data['real_date']}\n"
            f"session: {data['slug']}\n---\n\n"
        )
        summary_path.write_text(fm_header + summary_text, encoding="utf-8")

    return StreamingResponse(stream_and_save(), media_type="text/plain")


NARRATION_VOICE = os.environ.get("NARRATION_VOICE", "en-US-GuyNeural")

_VOICE_CACHE: list[dict] | None = None

@app.get("/api/voices")
async def list_voices():
    global _VOICE_CACHE
    if _VOICE_CACHE is None:
        all_voices = await edge_tts.list_voices()
        _VOICE_CACHE = [
            {"id": v["ShortName"], "label": v["FriendlyName"]}
            for v in all_voices
            if v["Locale"].startswith("en-")
        ]
        _VOICE_CACHE.sort(key=lambda v: v["id"])
    return {"voices": _VOICE_CACHE, "default": NARRATION_VOICE}


@app.post("/api/session/narrate")
async def narrate_session(payload: dict):
    path   = (payload.get("path") or "").strip()
    voice  = (payload.get("voice") or NARRATION_VOICE).strip()
    source = (payload.get("source") or "summary").strip()
    full = (CODEX_ROOT / path).resolve()
    if not str(full).startswith(str(CODEX_ROOT.resolve())) or not full.is_dir():
        raise HTTPException(404, "session not found")

    data = _load_session(full)
    if source == "notes":
        text = data["notes_body"]
        if not text:
            raise HTTPException(400, "no session notes found")
        out_file = full / "narration-notes.mp3"
    else:
        text = data["summary"]
        if not text:
            raise HTTPException(400, "no summary found — generate a summary first")
        out_file = full / "narration.mp3"

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(out_file))
    return {"path": path, "ready": True, "voice": voice, "source": source}


@app.get("/api/session/narration")
async def get_narration(path: str, source: str = "summary"):
    full = (CODEX_ROOT / path).resolve()
    if not str(full).startswith(str(CODEX_ROOT.resolve())) or not full.is_dir():
        raise HTTPException(404, "session not found")
    fname = "narration-notes.mp3" if source == "notes" else "narration.mp3"
    narration_path = full / fname
    if not narration_path.exists():
        raise HTTPException(404, "no narration yet")
    return FileResponse(str(narration_path), media_type="audio/mpeg")


@app.post("/api/arc/summarize")
async def summarize_arc(payload: dict):
    year = (payload.get("year") or "").strip()
    if not year.isdigit():
        raise HTTPException(400, "year must be a 4-digit number")
    if not await ollama_client.is_reachable():
        raise HTTPException(503, "Ollama not reachable")

    sessions = [s for s in _all_sessions() if s["year"] == year and s["has_summary"]]
    if not sessions:
        raise HTTPException(400, f"No session summaries found for {year}. Generate individual session summaries first.")

    sessions.sort(key=lambda s: s["real_date"])
    sessions_block = "\n\n---\n\n".join(
        f"SESSION {s['real_date']} (in-game: {s['in_game_date']}):\n{s['summary']}"
        for s in sessions
    )

    prompt = (
        f"The following are individual session summaries from a D&D campaign, all from {year}. "
        "Write a 3-4 paragraph arc summary covering the full year: what the party was trying to accomplish, "
        "the major events and turning points, key NPCs they dealt with, and where things stand at year's end. "
        "Write in past tense as a campaign chronicle. Do not invent details not present in the summaries.\n\n"
        f"SESSIONS:\n{sessions_block}"
    )

    arc_dir = CODEX_ROOT.parent / "codex" / "arcs"
    arc_path = CODEX_ROOT / "arcs" / f"{year}.md"
    chunks: list[str] = []

    async def stream_and_save():
        async for chunk in ollama_client.stream(prompt, system=SYSTEM_PROMPT):
            chunks.append(chunk)
            yield chunk
        arc_text = "".join(chunks)
        arc_path.parent.mkdir(parents=True, exist_ok=True)
        arc_path.write_text(
            f"---\nyear: {year}\nsessions: {len(sessions)}\n---\n\n{arc_text}",
            encoding="utf-8"
        )

    return StreamingResponse(stream_and_save(), media_type="text/plain")


def _update_scene_count(session_dir: Path, count: int):
    """Write scene_count back into the notes frontmatter."""
    notes_files = sorted(session_dir.glob("notes-*.md"))
    if not notes_files:
        return
    notes_path = notes_files[0]
    text = notes_path.read_text(encoding="utf-8")
    if _re.search(r"^scene_count:", text, flags=_re.MULTILINE):
        text = _re.sub(r"^scene_count: .*$", f"scene_count: {count}", text, flags=_re.MULTILINE)
    else:
        text = _re.sub(r"^(---\n)", rf"\1scene_count: {count}\n", text, count=1)
    notes_path.write_text(text, encoding="utf-8")


def _entity_slugify(name: str) -> str:
    s = _re.sub(r"[^\w\s-]", "", name.lower())
    s = _re.sub(r"[\s_]+", "-", s)
    return _re.sub(r"-+", "-", s).strip("-")[:80].rstrip("-")

def _session_date_from_slug(slug: str) -> str:
    return slug.split("_", 1)[0]

def _appearance_block(session_date: str, slug: str, evidence: list) -> str:
    lines = [f"### {session_date} ({slug})\n"]
    for ev in evidence:
        lines.append(f'- Scene {ev["scene"]}: _{ev["quote"]}_')
    return "\n".join(lines) + "\n"

def _create_entity_file_web(path, name: str, kind: str, description: str, evidence: list, session_slug: str,
                            allegiance: str = "", locations_seen: list | None = None,
                            carried_by: list | None = None, carried_items: list | None = None):
    session_date = _session_date_from_slug(session_slug)
    locs_yaml = _json.dumps(locations_seen or [])
    carried_by_yaml = _json.dumps(carried_by or [])
    carried_items_yaml = _json.dumps(carried_items or [])
    fm = (
        f"---\nname: {name}\ntype: {kind}\naliases: []\n"
        f"first_seen: {session_date}\nsessions: [{session_slug}]\ntags: []\nstatus: active\n"
        f"allegiance: {allegiance}\nlocations_seen: {locs_yaml}\n"
        f"carried_by: {carried_by_yaml}\ncarried_items: {carried_items_yaml}\n---\n"
    )
    body = f"\n## Description\n\n{description or name}\n\n## Appearances\n\n{_appearance_block(session_date, session_slug, evidence)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm + body, encoding="utf-8")

def _append_entity_appearance_web(path, evidence: list, session_slug: str):
    text = path.read_text(encoding="utf-8")
    if session_slug in text:
        return
    session_date = _session_date_from_slug(session_slug)
    # update sessions list in frontmatter
    text = _re.sub(r"^sessions: \[(.*)?\]", lambda m: f"sessions: [{m.group(1) + ', ' if m.group(1) else ''}{session_slug}]", text, flags=_re.MULTILINE)
    text = text.rstrip("\n") + "\n\n" + _appearance_block(session_date, session_slug, evidence) + "\n"
    path.write_text(text, encoding="utf-8")

def _create_thread_file_web(path, description: str, scene: int, session_slug: str):
    session_date = _session_date_from_slug(session_slug)
    fm = f"---\nname: {description}\ntype: thread\nstatus: open\nopened: {session_date}\nopened_scene: {scene}\nresolved: \nsessions: [{session_slug}]\ntags: []\n---\n"
    body = f"\n## Description\n\n{description}\n\n## Appearances\n\n### {session_date} ({session_slug})\n\n- Scene {scene}: _{description}_\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm + body, encoding="utf-8")

def _append_thread_appearance_web(path, description: str, scene: int, session_slug: str):
    text = path.read_text(encoding="utf-8")
    if session_slug in text:
        return
    session_date = _session_date_from_slug(session_slug)
    text = _re.sub(r"^sessions: \[(.*)?\]", lambda m: f"sessions: [{m.group(1) + ', ' if m.group(1) else ''}{session_slug}]", text, flags=_re.MULTILINE)
    text = text.rstrip("\n") + f"\n\n### {session_date} ({session_slug})\n\n- Scene {scene}: _{description}_\n"
    path.write_text(text, encoding="utf-8")


@app.post("/api/session/extract")
async def extract_session_entities(payload: dict):
    path = (payload.get("path") or "").strip()
    full = (CODEX_ROOT / path).resolve()
    if not str(full).startswith(str(CODEX_ROOT.resolve())) or not full.is_dir():
        raise HTTPException(404, "session not found")
    if not await ollama_client.is_reachable():
        raise HTTPException(503, "Ollama not reachable")
    data = _load_session(full)
    if not data["notes_body"]:
        raise HTTPException(400, "no session notes found")

    prompts_dir = ROOT / "backend" / "app" / "prompts"

    async def _stream():
        scenes = _scene_splitter.split_into_scenes(data["notes_body"])
        total = len(scenes)
        _update_scene_count(full, total)
        yield f"Found {total} scene{'s' if total != 1 else ''} in session notes\n"

        report = _extractors.ExtractionReport()
        npc_bucket: dict = {}
        loc_bucket: dict = {}
        item_bucket: dict = {}
        threads: list = []

        for scene in scenes:
            yield f"Extracting scene {scene.index + 1}/{total}: {scene.title[:50]}…\n"
            extracted, err = await _extractors.extract_scene(scene, prompts_dir)
            if extracted is None:
                yield f"  ⚠ scene {scene.index + 1} failed: {(err or '')[:80]}\n"
                report.parse_failures.append({"scene_index": scene.index, "title": scene.title, "error": err or ""})
                continue
            verified = _extractors._verify_extraction(extracted, scene.text, report, scene.index)
            report.scene_extractions.append((scene, verified))
            yield f"  → {len(verified.npcs)} NPCs, {len(verified.locations)} locations, {len(verified.items)} items, {len(verified.plot_threads_opened)} threads\n"

            for npc in verified.npcs:
                k = npc.name.strip()
                if k not in npc_bucket:
                    npc_bucket[k] = {"name": k, "description": npc.description, "evidence": [],
                                     "allegiance": npc.allegiance, "locations_seen": []}
                elif npc.allegiance and not npc_bucket[k]["allegiance"]:
                    npc_bucket[k]["allegiance"] = npc.allegiance
                npc_bucket[k]["evidence"].append({"scene": scene.index, "quote": npc.evidence_quote})
                for loc in npc.locations_seen:
                    if loc and loc not in npc_bucket[k]["locations_seen"]:
                        npc_bucket[k]["locations_seen"].append(loc)
            for loc in verified.locations:
                k = loc.name.strip()
                if k not in loc_bucket:
                    loc_bucket[k] = {"name": k, "description": loc.description, "evidence": []}
                loc_bucket[k]["evidence"].append({"scene": scene.index, "quote": loc.evidence_quote})
            for itm in verified.items:
                k = itm.name.strip()
                if k not in item_bucket:
                    item_bucket[k] = {"name": k, "description": itm.description, "evidence": [],
                                      "carried_by": itm.carried_by}
                elif itm.carried_by and not item_bucket[k]["carried_by"]:
                    item_bucket[k]["carried_by"] = itm.carried_by
                item_bucket[k]["evidence"].append({"scene": scene.index, "quote": itm.evidence_quote})
            for t in verified.plot_threads_opened:
                threads.append({"scene": scene.index, "description": t.thread})

        candidates = {
            "npcs": list(npc_bucket.values()),
            "locations": list(loc_bucket.values()),
            "items": list(item_bucket.values()),
            "threads": threads,
        }
        (full / "candidates.json").write_text(_json.dumps(candidates, indent=2), encoding="utf-8")
        total_found = sum(len(v) for v in candidates.values())
        yield f"\nExtraction complete — {total_found} entities found\n"
        yield f"__CANDIDATES_JSON__\n{_json.dumps(candidates)}\n"

    return StreamingResponse(_stream(), media_type="text/plain")


@app.get("/api/session/candidates")
async def get_candidates(path: str):
    full = (CODEX_ROOT / path).resolve()
    if not str(full).startswith(str(CODEX_ROOT.resolve())) or not full.is_dir():
        raise HTTPException(404, "session not found")
    cand_path = full / "candidates.json"
    if not cand_path.exists():
        raise HTTPException(404, "no candidates extracted yet")
    return _json.loads(cand_path.read_text(encoding="utf-8"))


_KIND_DIR_MAP = {"npc": "npcs", "npcs": "npcs", "location": "locations", "locations": "locations",
                 "item": "items", "items": "items", "thread": "plot-threads", "threads": "plot-threads"}


def _promote_candidates(session_slug: str, selections: list) -> tuple[int, int]:
    """Write a list of candidate selections to the codex. Returns (created, updated)."""
    created = updated = 0
    for sel in selections:
        kind = sel.get("kind", "")
        dir_name = _KIND_DIR_MAP.get(kind)
        if not dir_name:
            continue
        kind_dir = CODEX_ROOT / dir_name

        if kind in ("thread", "threads"):
            desc = sel.get("description", "")
            scene = int(sel.get("scene", 0))
            slug = _entity_slugify(desc)[:60]
            target = kind_dir / f"{slug}.md"
            if not target.exists():
                _create_thread_file_web(target, desc, scene, session_slug)
                created += 1
            else:
                _append_thread_appearance_web(target, desc, scene, session_slug)
                updated += 1
        else:
            name = sel.get("name", "")
            desc = sel.get("description", "")
            evidence = sel.get("evidence", [])
            allegiance = sel.get("allegiance", "")
            locations_seen = sel.get("locations_seen", [])
            carried_by = [sel.get("carried_by", "")] if sel.get("carried_by") else []
            slug = _entity_slugify(name)
            target = kind_dir / f"{slug}.md"
            kind_singular = kind.rstrip("s")
            if not target.exists():
                _create_entity_file_web(target, name, kind_singular, desc, evidence, session_slug,
                                        allegiance=allegiance, locations_seen=locations_seen,
                                        carried_by=carried_by)
                created += 1
            else:
                _append_entity_appearance_web(target, evidence, session_slug)
                if allegiance:
                    txt = target.read_text(encoding="utf-8")
                    if "allegiance: \n" in txt or "allegiance: ''" in txt or 'allegiance: ""' in txt:
                        txt = _re.sub(r"^allegiance: .*$", f"allegiance: {allegiance}", txt, flags=_re.MULTILINE)
                        target.write_text(txt, encoding="utf-8")
                if locations_seen:
                    txt = target.read_text(encoding="utf-8")
                    m = _re.search(r"^locations_seen: (\[.*?\])$", txt, flags=_re.MULTILINE)
                    if m:
                        try:
                            existing = _json.loads(m.group(1))
                            merged = list(dict.fromkeys(existing + locations_seen))
                            txt = txt[:m.start(1)] + _json.dumps(merged) + txt[m.end(1):]
                            target.write_text(txt, encoding="utf-8")
                        except Exception:
                            pass
                updated += 1
            if kind_singular == "item" and carried_by:
                npc_slug = _entity_slugify(carried_by[0])
                npc_path = CODEX_ROOT / "npcs" / f"{npc_slug}.md"
                if npc_path.exists():
                    txt = npc_path.read_text(encoding="utf-8")
                    m = _re.search(r"^carried_items: (\[.*?\])$", txt, flags=_re.MULTILINE)
                    if m:
                        try:
                            existing = _json.loads(m.group(1))
                            if name not in existing:
                                existing.append(name)
                                txt = txt[:m.start(1)] + _json.dumps(existing) + txt[m.end(1):]
                                npc_path.write_text(txt, encoding="utf-8")
                        except Exception:
                            pass
    return created, updated


@app.post("/api/session/promote")
async def promote_entities(payload: dict):
    path = (payload.get("path") or "").strip()
    selections = payload.get("selections", [])
    full = (CODEX_ROOT / path).resolve()
    if not str(full).startswith(str(CODEX_ROOT.resolve())) or not full.is_dir():
        raise HTTPException(404, "session not found")
    session_slug = full.name

    # Tag each selection with its kind key for the helper
    created, updated = _promote_candidates(session_slug, selections)
    indexer.reindex(CODEX_ROOT, DB_PATH)

    # Build entity list for UI response
    entities = []
    for sel in selections:
        kind = sel.get("kind", "")
        dir_name = _KIND_DIR_MAP.get(kind)
        if not dir_name:
            continue
        if kind in ("thread", "threads"):
            desc = sel.get("description", "")
            slug = _entity_slugify(desc)[:60]
            entities.append({"name": desc[:60], "action": "saved", "path": f"{dir_name}/{slug}.md"})
        else:
            name = sel.get("name", "")
            slug = _entity_slugify(name)
            entities.append({"name": name, "action": "saved", "path": f"{dir_name}/{slug}.md"})

    return {"created": created, "updated": updated, "entities": entities}


_VALID_STATUSES = {"open", "resolved", "dormant", "active"}


@app.post("/api/entity/status")
async def update_entity_status(payload: dict):
    path = (payload.get("path") or "").strip()
    status = (payload.get("status") or "").strip()
    if status not in _VALID_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(_VALID_STATUSES)}")
    full = (CODEX_ROOT / path).resolve()
    if not str(full).startswith(str(CODEX_ROOT.resolve())) or not full.exists():
        raise HTTPException(404, "entity not found")
    text = full.read_text(encoding="utf-8")
    text = _re.sub(r"^status: .*$", f"status: {status}", text, flags=_re.MULTILINE)
    full.write_text(text, encoding="utf-8")
    return {"path": path, "status": status}


_VALID_DISPOSITIONS = {"ally", "enemy", "neutral", "recurring", ""}


@app.post("/api/entity/disposition")
async def update_entity_disposition(payload: dict):
    path = (payload.get("path") or "").strip()
    disposition = (payload.get("disposition") or "").strip()
    if disposition not in _VALID_DISPOSITIONS:
        raise HTTPException(400, f"disposition must be one of {sorted(_VALID_DISPOSITIONS)}")
    full = (CODEX_ROOT / path).resolve()
    if not str(full).startswith(str(CODEX_ROOT.resolve())) or not full.exists():
        raise HTTPException(404, "entity not found")
    text = full.read_text(encoding="utf-8")
    if _re.search(r"^disposition:", text, flags=_re.MULTILINE):
        text = _re.sub(r"^disposition: .*$", f"disposition: {disposition}", text, flags=_re.MULTILINE)
    else:
        # Insert after status line
        text = _re.sub(r"^(status: .*)$", rf"\1\ndisposition: {disposition}", text, flags=_re.MULTILINE, count=1)
    full.write_text(text, encoding="utf-8")
    return {"path": path, "disposition": disposition}


@app.post("/api/entity/merge")
async def merge_entities(payload: dict):
    """Merge one or more entity files into a canonical target."""
    keep_path  = (payload.get("keep") or "").strip()
    others     = [p.strip() for p in payload.get("others", []) if p.strip()]
    canon_name = (payload.get("name") or "").strip()

    if not keep_path or not others:
        raise HTTPException(400, "keep and others are required")

    keep_full = (CODEX_ROOT / keep_path).resolve()
    if not str(keep_full).startswith(str(CODEX_ROOT.resolve())) or not keep_full.exists():
        raise HTTPException(404, f"keep entity not found: {keep_path}")

    keep_fm   = _fm.load(keep_full)
    keep_sessions = list(_re.findall(r"[\w\-]+", str(keep_fm.get("sessions", "[]"))))
    keep_aliases  = list(_re.findall(r"[\w\s\-']+", str(keep_fm.get("aliases", "[]"))))
    keep_aliases  = [a.strip() for a in keep_aliases if a.strip()]
    keep_body     = keep_fm.content

    for other_path in others:
        other_full = (CODEX_ROOT / other_path).resolve()
        if not str(other_full).startswith(str(CODEX_ROOT.resolve())) or not other_full.exists():
            continue
        other_fm = _fm.load(other_full)
        # Merge sessions
        for s in _re.findall(r"[\w\-]+", str(other_fm.get("sessions", "[]"))):
            if s and s not in keep_sessions:
                keep_sessions.append(s)
        # Add old name as alias
        old_name = str(other_fm.get("name", other_full.stem)).strip()
        if old_name and old_name not in keep_aliases and old_name != keep_fm.get("name", ""):
            keep_aliases.append(old_name)
        # Append appearances from other file
        other_body = other_fm.content
        in_app = False
        app_lines: list[str] = []
        for line in other_body.splitlines():
            if line.startswith("## Appearances"):
                in_app = True
                continue
            if line.startswith("## ") and in_app:
                break
            if in_app:
                app_lines.append(line)
        if app_lines:
            keep_body = keep_body.rstrip("\n") + "\n\n" + "\n".join(app_lines).strip() + "\n"
        other_full.unlink()

    # Write merged file
    final_name = canon_name or str(keep_fm.get("name", keep_full.stem))
    sessions_yaml = _json.dumps(keep_sessions)
    aliases_yaml  = _json.dumps(keep_aliases)

    raw = keep_full.read_text(encoding="utf-8")
    raw = _re.sub(r"^name: .*$",     f"name: {final_name}",       raw, flags=_re.MULTILINE)
    raw = _re.sub(r"^sessions: .*$", f"sessions: {sessions_yaml}", raw, flags=_re.MULTILINE)
    raw = _re.sub(r"^aliases: .*$",  f"aliases: {aliases_yaml}",   raw, flags=_re.MULTILINE)
    # Replace body (everything after closing ---)
    pre, _, _ = raw.partition("---\n\n")
    fm_block = raw[:raw.index("---\n\n") + 5]
    keep_full.write_text(fm_block + keep_body, encoding="utf-8")

    indexer.reindex(CODEX_ROOT, DB_PATH)
    return {"kept": keep_path, "merged": len(others), "name": final_name}


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    return templates.TemplateResponse(request, "upload.html", {})


@app.post("/api/upload/notes")
async def upload_notes(
    file: UploadFile,
    notetaker: str = Form(""),
    slug: str = Form(""),
):
    fname = file.filename or ""
    if not fname.lower().endswith((".md", ".txt")):
        raise HTTPException(400, "File must be a .md or .txt file")

    content = (await file.read()).decode("utf-8", errors="replace")

    # Try to pull metadata from frontmatter
    fm_meta: dict = {}
    try:
        fm_obj = _fm.loads(content)
        fm_meta = {k: str(v) for k, v in fm_obj.metadata.items()}
    except Exception:
        pass

    if not notetaker:
        notetaker = fm_meta.get("notetaker", "unknown")

    safe_notetaker = _re.sub(r"[^a-z0-9]", "-", notetaker.lower()).strip("-") or "unknown"

    if not slug:
        stem = _re.sub(r"\.(md|txt)$", "", fname, flags=_re.IGNORECASE)
        stem = _re.sub(r"^notes-", "", stem, flags=_re.IGNORECASE)
        slug = _re.sub(r"[^a-z0-9_-]", "-", stem.lower())
        slug = _re.sub(r"-+", "-", slug).strip("-")
        real_date = fm_meta.get("real_date", "")
        if real_date and not slug.startswith(real_date):
            slug = f"{real_date}_{slug}"

    slug = _re.sub(r"[^a-z0-9_\-]", "", slug)
    if not slug:
        raise HTTPException(400, "Could not derive a session slug — provide one explicitly")

    session_dir = CODEX_ROOT / "sessions" / slug
    session_dir.mkdir(parents=True, exist_ok=True)

    notes_path = session_dir / f"notes-{safe_notetaker}.md"
    notes_path.write_text(content, encoding="utf-8")

    indexer.reindex(CODEX_ROOT, DB_PATH)

    return JSONResponse({"slug": slug, "path": f"sessions/{slug}"})


_SLUG_STOP = frozenset({
    "the","and","for","with","was","are","his","her","they","that","this",
    "from","have","had","but","not","she","him","into","over","also","just",
    "then","been","when","were","said","each","which","their","time","will",
})

def _slug_words(text: str, max_words: int = 5) -> str:
    words = _re.findall(r"[a-z]+", text.lower())
    words = [w for w in words if len(w) > 2 and w not in _SLUG_STOP]
    return "-".join(words[:max_words])


def _split_sessions(content: str) -> list[dict]:
    """Split a multi-session document into individual session dicts."""
    lines = content.splitlines()
    sessions: list[dict] = []
    cur_lines: list[str] = []
    cur_month = cur_day = cur_year = None
    last_year: int | None = None
    last_month: int | None = None

    for raw_line in lines:
        line = raw_line.rstrip()
        # Strip leading/trailing noise (backslash, star, dash, space) then test for date
        stripped = _re.sub(r"^[\\*\-\s]+", "", line)
        stripped = _re.sub(r"[\\*\-\s]+$", "", stripped)
        m = _re.match(r"^(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?", stripped)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12 and 1 <= day <= 31:
                yr_str = m.group(3)
                if yr_str:
                    year: int = int(yr_str)
                    if year < 100:
                        year += 2000
                    last_year = year
                else:
                    # Infer year: if month wrapped back significantly, bump year
                    if last_year is None:
                        year = 2019
                    elif last_month is not None and month < last_month - 2:
                        year = last_year + 1
                    else:
                        year = last_year
                    last_year = year

                last_month = month

                # Flush previous block
                if any(l.strip() for l in cur_lines):
                    sessions.append({
                        "month": cur_month, "day": cur_day, "year": cur_year,
                        "lines": cur_lines,
                        "header_tail": "",
                    })
                # Grab any description text after the date in the header
                header_tail = stripped[m.end():]
                header_tail = _re.sub(r"^[\s\\*\-]+", "", header_tail)
                header_tail = _re.sub(r"[\s\\*\-]+$", "", header_tail)

                cur_month, cur_day, cur_year = month, day, year
                cur_lines = []
                # Store tail for slug use, will be set on flush
                _tail = header_tail
                continue

        cur_lines.append(line)

    if any(l.strip() for l in cur_lines):
        sessions.append({
            "month": cur_month, "day": cur_day, "year": cur_year,
            "lines": cur_lines,
            "header_tail": "",
        })

    return sessions


@app.post("/api/upload/split")
async def split_upload(
    file: UploadFile,
    notetaker: str = Form(""),
):
    fname = file.filename or ""
    if not fname.lower().endswith((".md", ".txt")):
        raise HTTPException(400, "File must be a .md or .txt file")

    content = (await file.read()).decode("utf-8", errors="replace")

    if not notetaker:
        try:
            fm_obj = _fm.loads(content)
            notetaker = str(fm_obj.get("notetaker", "")) or ""
        except Exception:
            pass
    if not notetaker:
        notetaker = "unknown"
    safe_notetaker = _re.sub(r"[^a-z0-9]", "-", notetaker.lower()).strip("-") or "unknown"

    sessions = _split_sessions(content)
    if not sessions:
        raise HTTPException(400, "No content found in file")

    created: list[dict] = []
    skipped: list[dict] = []

    for s in sessions:
        year, month, day = s["year"], s["month"], s["day"]
        if year and month and day:
            date_str = f"{year:04d}-{month:02d}-{day:02d}"
        else:
            date_str = "0000-00-00"

        # Build slug: prefer first content words, fall back to date only
        desc = _slug_words(" ".join(s["lines"][:5]))
        base_slug = f"{date_str}_{desc}" if desc else date_str
        base_slug = _re.sub(r"[^a-z0-9_\-]", "", base_slug)[:80]

        # Ensure unique slug
        slug = base_slug
        counter = 2
        while (CODEX_ROOT / "sessions" / slug).exists():
            slug = f"{base_slug}-v{counter}"
            counter += 1

        session_dir = CODEX_ROOT / "sessions" / slug
        session_dir.mkdir(parents=True, exist_ok=True)

        fm_header = (
            f"---\n"
            f"real_date: {date_str}\n"
            f"notetaker: {notetaker}\n"
            f"scene_count: 0\n"
            f"---\n\n"
        )
        notes_path = session_dir / f"notes-{safe_notetaker}.md"
        notes_path.write_text(fm_header + "\n".join(s["lines"]), encoding="utf-8")
        created.append({"slug": slug, "date": date_str})

    indexer.reindex(CODEX_ROOT, DB_PATH)
    return JSONResponse({"sessions_created": len(created), "sessions_skipped": len(skipped), "sessions": created})


@app.post("/api/bulk/process")
async def bulk_process(payload: dict):
    """Stream progress while summarizing + extracting all (or unprocessed) sessions."""
    skip_existing = bool(payload.get("skip_existing", True))
    auto_promote  = bool(payload.get("auto_promote", False))
    if not await ollama_client.is_reachable():
        raise HTTPException(503, "Ollama not reachable")

    prompts_dir = ROOT / "backend" / "app" / "prompts"

    async def _stream():
        sessions = _all_sessions()
        sessions_sorted = sorted(sessions, key=lambda s: s["real_date"])
        total = len(sessions_sorted)
        yield f"Found {total} sessions\n"

        done = skipped = failed = 0

        for idx, s in enumerate(sessions_sorted, 1):
            session_dir = CODEX_ROOT / "sessions" / s["slug"]
            yield f"\n[{idx}/{total}] {s['real_date']} — {s['slug']}\n"

            # ── Summarize ───────────────────────────────────────────────────
            if s["has_summary"] and skip_existing:
                yield "  ✓ summary exists, skipping\n"
            else:
                yield "  ✦ generating summary…\n"
                data = _load_session(session_dir)
                if not data["notes_body"]:
                    yield "  ⚠ no notes found, skipping\n"
                    failed += 1
                    continue

                prompt = (
                    "Write a 2-3 paragraph narrative session recap in past tense, as if briefing a player who missed the session. "
                    "Cover: what the party investigated or accomplished, key NPCs they met, any combat or significant skill checks, "
                    "and which plot threads advanced or opened. Use the character names exactly as written. "
                    "Do not invent any details not present in the notes. Do not include stat blocks or canonical lore.\n\n"
                    f"REAL DATE: {data['real_date']}\n"
                    f"IN-GAME DATE: {data['in_game_date']}\n\n"
                    f"SESSION NOTES:\n{data['notes_body']}"
                )
                try:
                    chunks: list[str] = []
                    async for chunk in ollama_client.stream(prompt, system=SYSTEM_PROMPT):
                        chunks.append(chunk)
                    summary_text = "".join(chunks)
                    fm_header = f"---\ngenerated: {data['real_date']}\nsession: {data['slug']}\n---\n\n"
                    (session_dir / "summary.md").write_text(fm_header + summary_text, encoding="utf-8")
                    yield f"  ✓ summary saved ({len(summary_text)} chars)\n"
                except Exception as exc:
                    yield f"  ✗ summary failed: {exc}\n"
                    failed += 1
                    continue

            # ── Extract entities ────────────────────────────────────────────
            if (session_dir / "candidates.json").exists() and skip_existing:
                yield "  ✓ candidates exist, skipping\n"
                done += 1
                continue

            yield "  ✦ extracting entities…\n"
            data = _load_session(session_dir)
            try:
                scenes = _scene_splitter.split_into_scenes(data["notes_body"])
                _update_scene_count(session_dir, len(scenes))
                yield f"  → {len(scenes)} scenes\n"

                report = _extractors.ExtractionReport()
                npc_bucket: dict = {}
                loc_bucket: dict = {}
                item_bucket: dict = {}
                threads: list = []

                for scene in scenes:
                    extracted, err = await _extractors.extract_scene(scene, prompts_dir)
                    if extracted is None:
                        report.parse_failures.append({"scene_index": scene.index, "title": scene.title, "error": err or ""})
                        continue
                    verified = _extractors._verify_extraction(extracted, scene.text, report, scene.index)
                    for npc in verified.npcs:
                        k = npc.name.strip()
                        if k not in npc_bucket:
                            npc_bucket[k] = {"name": k, "description": npc.description, "evidence": [],
                                             "allegiance": npc.allegiance, "locations_seen": []}
                        elif npc.allegiance and not npc_bucket[k]["allegiance"]:
                            npc_bucket[k]["allegiance"] = npc.allegiance
                        npc_bucket[k]["evidence"].append({"scene": scene.index, "quote": npc.evidence_quote})
                        for loc in npc.locations_seen:
                            if loc and loc not in npc_bucket[k]["locations_seen"]:
                                npc_bucket[k]["locations_seen"].append(loc)
                    for loc in verified.locations:
                        k = loc.name.strip()
                        if k not in loc_bucket:
                            loc_bucket[k] = {"name": k, "description": loc.description, "evidence": []}
                        loc_bucket[k]["evidence"].append({"scene": scene.index, "quote": loc.evidence_quote})
                    for itm in verified.items:
                        k = itm.name.strip()
                        if k not in item_bucket:
                            item_bucket[k] = {"name": k, "description": itm.description, "evidence": [],
                                              "carried_by": itm.carried_by}
                        elif itm.carried_by and not item_bucket[k]["carried_by"]:
                            item_bucket[k]["carried_by"] = itm.carried_by
                        item_bucket[k]["evidence"].append({"scene": scene.index, "quote": itm.evidence_quote})
                    for t in verified.plot_threads_opened:
                        threads.append({"scene": scene.index, "description": t.thread})

                candidates = {
                    "npcs": list(npc_bucket.values()),
                    "locations": list(loc_bucket.values()),
                    "items": list(item_bucket.values()),
                    "threads": threads,
                }
                (session_dir / "candidates.json").write_text(_json.dumps(candidates, indent=2), encoding="utf-8")
                total_found = sum(len(v) for v in candidates.values())
                yield f"  ✓ {total_found} entities found\n"

                if auto_promote:
                    yield f"  ✦ promoting to codex…\n"
                    selections = []
                    for npc in candidates["npcs"]:
                        selections.append({**npc, "kind": "npcs"})
                    for loc in candidates["locations"]:
                        selections.append({**loc, "kind": "locations"})
                    for itm in candidates["items"]:
                        selections.append({**itm, "kind": "items"})
                    for t in candidates["threads"]:
                        selections.append({**t, "kind": "threads"})
                    try:
                        c, u = _promote_candidates(s["slug"], selections)
                        yield f"  ✓ promoted: {c} created, {u} updated\n"
                    except Exception as exc:
                        yield f"  ✗ promote failed: {exc}\n"

                done += 1
            except Exception as exc:
                yield f"  ✗ extraction failed: {exc}\n"
                failed += 1

        if auto_promote:
            indexer.reindex(CODEX_ROOT, DB_PATH)
        yield f"\n═══ Bulk process complete: {done} done, {skipped} skipped, {failed} failed ═══\n"

    return StreamingResponse(_stream(), media_type="text/plain")


@app.post("/api/reindex")
async def api_reindex():
    n = indexer.reindex(CODEX_ROOT, DB_PATH)
    return {"indexed": n}


@app.post("/api/ask")
async def api_ask(payload: dict):
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "question required")
    if not await ollama_client.is_reachable():
        raise HTTPException(503, f"Ollama not reachable at {ollama_client.OLLAMA_URL}")

    conn = _db()
    try:
        hits = db.search(conn, _question_to_fts(question), limit=8)
    finally:
        conn.close()

    context_block = "\n\n---\n\n".join(
        f"[{h['kind']}] {h['title']} ({h['path']})\n{h['snippet']}"
        for h in hits
    ) or "(no relevant snippets found)"

    prompt = (
        "Answer the user's question using only the retrieved context below. "
        "Follow the system constraints strictly.\n\n"
        f"RETRIEVED CONTEXT:\n{context_block}\n\n"
        f"USER QUESTION: {question}\n\n"
        "ANSWER:"
    )

    async def stream_response():
        async for chunk in ollama_client.stream(prompt, system=SYSTEM_PROMPT):
            yield chunk

    return StreamingResponse(stream_response(), media_type="text/plain")
