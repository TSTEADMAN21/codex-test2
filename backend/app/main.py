"""FastAPI entrypoint for the adventure-codex backend."""
from __future__ import annotations
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, indexer, ollama_client

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
              for kind in ("session", "npc", "location", "event", "item", "faction", "party")}
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


@app.get("/api/document")
async def api_document(path: str):
    full = (CODEX_ROOT / path).resolve()
    if not str(full).startswith(str(CODEX_ROOT.resolve())) or not full.exists():
        raise HTTPException(404, "not found")
    return {"path": path, "body": full.read_text(encoding="utf-8")}


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
        hits = db.search(conn, question, limit=8)
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
