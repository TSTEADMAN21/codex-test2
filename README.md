# Adventure Codex

A free, local, homebrew-safe player AID for a Dungeons & Dragons campaign. Ingests raw session notes written in any style, produces a searchable codex of NPCs / locations / events / items, and answers questions using a local LLM so you spend zero dollars on routine lookups.

## What it does today (MVP)

1. **Normalize** raw notes — fixes encoding artifacts (smart-quote mojibake, weird whitespace).
2. **Split** prose into scenes using heuristics (no AI required).
3. **Surface entity candidates** (NPCs, locations, items) for human review — nothing is promoted into the codex without you checking a box.
4. **Flag disguise / pseudonym lines** so fake names used in-character don't get indexed as real aliases.
5. **Index** everything into SQLite FTS5 for fast full-text search.
6. **Web UI** with search + "Ask" (local Ollama LLM) backed by retrieved log snippets.
7. **Anti-lore system prompt** — allows commoner-tier Forgotten Realms common knowledge (wards, famous landmarks) but strictly forbids plot, stat-block, or module-specific details that would spoil the DM's homebrew.

## What it deliberately does *not* do yet

- LLM-powered entity extraction (Phase 2 — local Ollama can do it, but slowly; Claude API can too, for $).
- Multi-notetaker merge agent.
- Per-character player-POV filtering.
- Image generation for scenes.
- Arc rollups.

These are next — the scaffold is designed to extend cleanly.

---

## Quickstart (local Python, simplest path)

Requires Python 3.11+. No Docker needed.

```bash
cd ~/Documents/adventure-codex

python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .

# Optional but recommended: install Ollama for free local Q&A.
# https://ollama.com/download (or: brew install ollama)
ollama pull llama3.1:8b
ollama serve &  # starts the Ollama HTTP API on :11434

# Ingest the sample session notes.
python scripts/ingest.py raw-notes/testing.txt --notetaker travis

# Build the search index.
python -c "from pathlib import Path; from app import indexer; print(indexer.reindex(Path('codex'), Path('data/codex.db')), 'documents indexed')"

# Serve the UI.
uvicorn app.main:app --app-dir backend --reload
# open http://localhost:8000
```

The search box works without Ollama. The "Ask" section requires Ollama running.

## Quickstart (Docker, portable path)

Run the backend in a container; Ollama stays on the host (faster, uses your GPU).

```bash
# One-time: install + pull on host.
brew install ollama                 # macOS
ollama pull llama3.1:8b
ollama serve &

# Build and run the web app.
docker compose up --build
```

The container reaches host Ollama via `host.docker.internal:11434`. On Linux the `extra_hosts` entry in `docker-compose.yml` handles that; on macOS and Windows Docker Desktop provides it natively.

To expose the app to your D&D party, install [Tailscale](https://tailscale.com/) on the host and on each party member's device, and share `http://<host-tailscale-name>:8000` — no port-forwarding, no auth bolt-on.

---

## Ingesting a session

```bash
python scripts/ingest.py <raw-notes-file> --notetaker <player-name>
```

Output: `codex/sessions/<date>_<slug>/`
- `notes-<player>.md` — normalized raw notes with scene splits and frontmatter
- `candidates.md` — NPC / location / item candidates + disguise flags, for review
- `merged.md` — stub, to be filled in later by the multi-notetaker merge pass

**Nothing** is written to `codex/npcs/`, `codex/locations/`, etc. automatically. You promote candidates by editing `candidates.md`, creating the entity markdown, and rerunning reindex. This is deliberate — it preserves the anti-spoiler / anti-lore constraint.

---

## Repo layout

```
adventure-codex/
├── backend/app/          # FastAPI + SQLite + Ollama client
│   ├── normalizer.py        UTF-8 + whitespace cleanup, date detection
│   ├── scene_splitter.py    heuristic scene detection
│   ├── candidates.py        entity candidate extraction
│   ├── ingest.py            write session markdown files
│   ├── db.py                SQLite FTS5
│   ├── indexer.py           walk codex/, upsert into FTS
│   ├── ollama_client.py     httpx client for local LLM
│   ├── main.py              FastAPI routes
│   └── prompts/
│       └── system_constraints.md   the anti-lore / homebrew rules
├── frontend/             # HTMX + vanilla JS
├── codex/                # your data as markdown (the canonical source)
├── raw-notes/            # drop raw session files here
├── data/                 # SQLite lives here (gitignored)
└── scripts/ingest.py
```

The markdown under `codex/` is the source of truth — the SQLite database is derived and can be rebuilt at any time via `POST /api/reindex` or by deleting `data/codex.db` and reindexing.

## Cost

Zero, if you run Ollama locally. The design deliberately keeps Claude-API use optional — you can add it later as an "Ask Claude" button for hard questions, toggled by environment variable. The default path burns no tokens.

## Design constraints baked in

- **Homebrew-first.** The `system_constraints.md` prompt disallows plot / stat-block / module-specific canonical content; it allows commoner-tier common knowledge (wards, famous landmarks).
- **Outsider perspective.** The party members are outsiders to Waterdeep; the prompt tells the LLM not to present insider knowledge as if the party had it.
- **Disguise-safe.** Pseudonyms used in-character are not added as aliases automatically.

See [backend/app/prompts/system_constraints.md](backend/app/prompts/system_constraints.md) for the full ruleset.
