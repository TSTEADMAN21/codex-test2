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
