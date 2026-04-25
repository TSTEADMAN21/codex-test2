"""Async wrapper around the Kokoro TTS subprocess generator."""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_PYTHON = _ROOT / "kokoro-venv" / "bin" / "python3"
_SCRIPT = _ROOT / "scripts" / "kokoro_generate.py"

DEFAULT_VOICE = "bm_fable"

VOICES: list[dict] = [
    # ── Kokoro (local, dramatic) ──────────────────────────────────────────
    {"id": "bm_fable",   "label": "Fable    · Kokoro · British, dramatic"},
    {"id": "bm_george",  "label": "George   · Kokoro · British"},
    {"id": "bm_daniel",  "label": "Daniel   · Kokoro · British"},
    {"id": "bm_lewis",   "label": "Lewis    · Kokoro · British"},
    {"id": "am_echo",    "label": "Echo     · Kokoro · American"},
    {"id": "am_eric",    "label": "Eric     · Kokoro · American"},
    {"id": "am_michael", "label": "Michael  · Kokoro · American"},
    {"id": "am_onyx",    "label": "Onyx     · Kokoro · American"},
    {"id": "af_jessica", "label": "Jessica  · Kokoro · American"},
    {"id": "af_bella",   "label": "Bella    · Kokoro · American"},
    {"id": "bf_emma",    "label": "Emma     · Kokoro · British"},
    # ── Edge TTS (cloud, better proper-noun pronunciation) ───────────────
    {"id": "en-US-GuyNeural",         "label": "Guy         · Edge · American"},
    {"id": "en-US-ChristopherNeural", "label": "Christopher · Edge · American"},
    {"id": "en-GB-RyanNeural",        "label": "Ryan        · Edge · British"},
    {"id": "en-GB-ThomasNeural",      "label": "Thomas      · Edge · British"},
    {"id": "en-US-EricNeural",        "label": "Eric        · Edge · American"},
    {"id": "en-US-AriaNeural",        "label": "Aria        · Edge · American"},
]


def is_edge_voice(voice_id: str) -> bool:
    return voice_id.startswith("en-")


async def generate(text: str, output_path: Path, voice: str = DEFAULT_VOICE, speed: float = 0.95) -> None:
    """Generate speech and write to output_path. Raises RuntimeError on failure."""
    proc = await asyncio.create_subprocess_exec(
        str(_PYTHON), str(_SCRIPT),
        "--text",   text,
        "--voice",  voice,
        "--output", str(output_path),
        "--speed",  str(speed),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Kokoro generation failed: {stderr.decode()[:300]}")
