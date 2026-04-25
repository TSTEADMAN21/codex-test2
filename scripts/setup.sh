#!/usr/bin/env bash
# One-command setup for a new host.
#
#   ./scripts/setup.sh
#
# Detects the OS, installs Ollama if missing, pulls the model, creates a
# Python venv, installs dependencies, reindexes the codex, and prints the
# command to start the server.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODEL="${OLLAMA_MODEL:-llama3.1:8b}"

color() { printf "\033[%sm%s\033[0m\n" "$1" "$2"; }
info() { color "36" "[setup] $*"; }
warn() { color "33" "[setup] $*"; }
die()  { color "31" "[setup] $*"; exit 1; }

info "Repo root: $REPO_ROOT"

# --- Python ---
if ! command -v python3 >/dev/null 2>&1; then
    die "python3 is required but not installed. Install Python 3.9+ and re-run."
fi
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python $PYVER detected."

if [[ ! -d .venv ]]; then
    info "Creating virtualenv at .venv/"
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

info "Installing Python dependencies..."
pip install -q --upgrade pip >/dev/null 2>&1 || true
pip install -q -e . >/dev/null

# --- Ollama ---
if ! command -v ollama >/dev/null 2>&1; then
    case "$OSTYPE" in
        darwin*)
            if command -v brew >/dev/null 2>&1; then
                info "Installing Ollama via Homebrew..."
                brew install ollama
            else
                die "Install Homebrew (https://brew.sh) then re-run, or download Ollama from https://ollama.com/download"
            fi
            ;;
        linux*)
            info "Installing Ollama via curl..."
            curl -fsSL https://ollama.com/install.sh | sh
            ;;
        msys*|cygwin*|win*)
            die "On Windows: install Ollama from https://ollama.com/download then re-run this script from WSL or Git Bash."
            ;;
        *)
            die "Unknown OS $OSTYPE. Install Ollama manually from https://ollama.com/download"
            ;;
    esac
else
    info "Ollama already installed: $(ollama --version 2>&1 | head -1)"
fi

# Make sure the Ollama server is reachable.
if ! curl -s -f http://localhost:11434/api/tags >/dev/null 2>&1; then
    warn "Ollama server not responding on :11434. Starting it in the background..."
    nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
    for i in {1..10}; do
        sleep 1
        if curl -s -f http://localhost:11434/api/tags >/dev/null 2>&1; then
            info "Ollama server is up."
            break
        fi
    done
    if ! curl -s -f http://localhost:11434/api/tags >/dev/null 2>&1; then
        die "Ollama server failed to start. Check /tmp/ollama-serve.log"
    fi
fi

# --- Pull the model ---
if ! ollama list | awk 'NR>1 {print $1}' | grep -qx "$MODEL"; then
    info "Pulling model: $MODEL (this may take several minutes on first install)"
    ollama pull "$MODEL"
else
    info "Model $MODEL already present."
fi

# --- Kokoro TTS ---
info "Setting up Kokoro TTS narration engine..."

KOKORO_VENV="$REPO_ROOT/backend/kokoro-venv"
MODELS_DIR="$REPO_ROOT/backend/models"
ONNX_FILE="$MODELS_DIR/kokoro-v1.0.onnx"
VOICES_FILE="$MODELS_DIR/voices-v1.0.bin"

# Find Python 3.10+ for the Kokoro venv (kokoro-onnx requires onnxruntime>=1.20.1)
KOKORO_PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1; then
        KOKORO_PYTHON="$(command -v "$candidate")"
        info "Found $candidate for Kokoro venv."
        break
    fi
done
if [[ -z "$KOKORO_PYTHON" ]]; then
    case "$OSTYPE" in
        darwin*)
            if command -v brew >/dev/null 2>&1; then
                info "Installing Python 3.14 via Homebrew for Kokoro..."
                brew install python@3.14
                KOKORO_PYTHON="$(brew --prefix python@3.14)/bin/python3.14"
            else
                warn "Python 3.10+ not found and Homebrew unavailable. Kokoro TTS will not be available."
                warn "Install Python 3.10+ and re-run setup.sh to enable narration."
                KOKORO_PYTHON=""
            fi
            ;;
        *)
            warn "Python 3.10+ not found. Kokoro TTS will not be available."
            warn "Install Python 3.10+ and re-run setup.sh to enable narration."
            KOKORO_PYTHON=""
            ;;
    esac
fi

if [[ -n "$KOKORO_PYTHON" ]]; then
    # Create Kokoro venv if missing
    if [[ ! -d "$KOKORO_VENV" ]]; then
        info "Creating Kokoro venv at backend/kokoro-venv/"
        "$KOKORO_PYTHON" -m venv "$KOKORO_VENV"
    fi

    info "Installing kokoro-onnx into Kokoro venv..."
    "$KOKORO_VENV/bin/pip" install -q --upgrade pip >/dev/null 2>&1 || true
    "$KOKORO_VENV/bin/pip" install -q kokoro-onnx soundfile

    # Download model files if missing
    mkdir -p "$MODELS_DIR"
    BASE_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
    if [[ ! -f "$ONNX_FILE" ]]; then
        info "Downloading Kokoro model (310 MB)..."
        curl -L -o "$ONNX_FILE" "$BASE_URL/kokoro-v1.0.onnx"
    else
        info "Kokoro model already present."
    fi
    if [[ ! -f "$VOICES_FILE" ]]; then
        info "Downloading Kokoro voices (27 MB)..."
        curl -L -o "$VOICES_FILE" "$BASE_URL/voices-v1.0.bin"
    else
        info "Kokoro voices already present."
    fi
    info "Kokoro TTS ready."
else
    warn "Skipping Kokoro TTS setup — narration will not work until Python 3.10+ is installed."
fi

# --- Build the search index ---
info "Building search index from codex/ ..."
python - <<'PY'
import sys
sys.path.insert(0, "backend")
from pathlib import Path
from app import indexer
n = indexer.reindex(Path("codex"), Path("data/codex.db"))
print(f"  indexed {n} documents")
PY

info "Setup complete."
echo
color "32" "Next steps:"
echo "  1. Start the web app:  uvicorn app.main:app --app-dir backend --reload"
echo "  2. Open:               http://localhost:8000"
echo
echo "  Or run under Docker:    docker compose up --build"
echo "  Or all-in-one Docker:   docker compose -f docker-compose.bundled.yml up --build"
