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

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import frontmatter as _fm

from . import db, entity_reader, indexer, ollama_client

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
