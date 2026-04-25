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

## Quickstart — pick one

Three run paths, ordered from simplest to most portable.

### Path A: One-command setup (recommended for first run on any host)

```bash
./scripts/setup.sh
```

Detects your OS, installs Ollama if missing, pulls `llama3.1:8b`, creates a Python venv, installs dependencies, builds the search index, and sets up the Kokoro TTS narration engine (downloads ~340 MB of model files on first run). Works on macOS and Linux. On Windows, run it from WSL or Git Bash.

> **Note:** Kokoro TTS requires Python 3.10+. The script will install it via Homebrew on macOS if needed. If you skip narration, everything else works fine.

Then:
```bash
source .venv/bin/activate
uvicorn app.main:app --app-dir backend --reload
# open http://localhost:8000
```

### Path B: Docker with host Ollama (faster — uses your GPU)

Ollama runs on the host, the web app runs in a container. On Apple Silicon this uses Metal inference (fast).

```bash
brew install ollama                 # macOS
ollama pull llama3.1:8b
ollama serve &
docker compose up --build
```

### Path C: Docker all-in-one (slowest, most portable)

Ollama + the model are baked into the container image. Larger image (~5 GB, because the model is pre-pulled at build time), slower inference (CPU-only), but zero host setup beyond Docker. Good for a weak laptop, a cheap VPS, or anywhere you don't want to install Ollama separately.

```bash
docker compose -f docker-compose.bundled.yml up --build
```

The first `build` will take 10–15 minutes (downloads + bakes in the model). After that `up` is fast.

---

## Ingesting notes after setup

```bash
source .venv/bin/activate
python scripts/ingest.py raw-notes/session.txt --notetaker dm          # heuristic (instant)
python scripts/ingest.py raw-notes/session.txt --notetaker dm --use-llm  # LLM (5+ min, far better)
```

## Sharing with your party

Install [Tailscale](https://tailscale.com/) on the host machine and on each party member's device. Share `http://<host-tailscale-name>:8000` — no port-forwarding, no auth setup needed, all traffic is encrypted through the Tailscale mesh.

---

## Ingesting a session

```bash
python scripts/ingest.py <raw-notes-file> --notetaker <player-name>
```

Output: `codex/sessions/<date>_<slug>/`
- `notes-<player>.md` — normalized raw notes with scene splits and frontmatter
- `candidates.md` — NPC / location / item candidates + disguise flags, for review
- `merged.md` — stub, to be filled in later by the multi-notetaker merge pass

**Nothing** is written to `codex/npcs/`, `codex/locations/`, etc. automatically. This is deliberate — it preserves the anti-spoiler / anti-lore constraint.

### Promoting candidates

1. Open `candidates_llm.md` and change `[ ]` to `[x]` for each entity you want to keep.
2. Run the promote script:

```bash
python scripts/promote.py codex/sessions/<date>_<slug>/candidates_llm.md
```

This creates `codex/npcs/<slug>.md`, `codex/locations/<slug>.md`, or `codex/items/<slug>.md` for each checked entity. If the file already exists (entity appears in a later session too), it appends a new appearance block instead of overwriting.

Use `--dry-run` to preview what would be created without writing anything.

3. Reindex so the new entity files are searchable:

```bash
# Via HTTP (server running):
curl -X POST http://localhost:8000/api/reindex

# Or directly:
python -c "import sys; sys.path.insert(0,'backend'); from pathlib import Path; from app import indexer; indexer.reindex(Path('codex'), Path('data/codex.db'))"
```

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
