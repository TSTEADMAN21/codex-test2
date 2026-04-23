"""Minimal Ollama HTTP client. No dependencies beyond httpx."""
from __future__ import annotations
import os
from typing import AsyncIterator

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")


async def generate(prompt: str, *, system: str | None = None,
                   model: str | None = None) -> str:
    payload = {
        "model": model or OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    if system:
        payload["system"] = system
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json().get("response", "")


async def stream(prompt: str, *, system: str | None = None,
                 model: str | None = None) -> AsyncIterator[str]:
    payload = {
        "model": model or OLLAMA_MODEL,
        "prompt": prompt,
        "stream": True,
    }
    if system:
        payload["system"] = system
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", f"{OLLAMA_URL}/api/generate", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                import json
                data = json.loads(line)
                if chunk := data.get("response"):
                    yield chunk
                if data.get("done"):
                    break


async def is_reachable() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False
