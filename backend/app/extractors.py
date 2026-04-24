"""LLM-driven entity extraction from session scenes.

Agentic loop: for each scene, ask the local LLM to return structured JSON
with entities + mandatory evidence quotes. Programmatically verify each
quote against the scene text and drop any entity whose evidence can't be
found (anti-hallucination guard).
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import httpx
from pydantic import BaseModel, Field, ValidationError

from . import ollama_client, scene_splitter

PARTY_MEMBERS_LOWER = {
    "selise", "ivy", "gororook", "rowin", "elliandis",
    "goro", "gor", "ell", "eli", "elli", "bororook",
}


class _Entity(BaseModel):
    name: str = ""
    description: str = ""
    evidence_quote: str


class NPC(_Entity):
    first_seen_this_scene: bool = True
    allegiance: str = ""
    locations_seen: List[str] = Field(default_factory=list)


class Location(_Entity):
    pass


class Item(_Entity):
    carried_by: str = ""


class Event(BaseModel):
    summary: str
    evidence_quote: str


class PlotThread(BaseModel):
    thread: str
    evidence_quote: str


class DisguiseAlert(BaseModel):
    fake_name: str
    real_identity: str
    evidence_quote: str


class SceneExtraction(BaseModel):
    npcs: List[NPC] = Field(default_factory=list)
    locations: List[Location] = Field(default_factory=list)
    items: List[Item] = Field(default_factory=list)
    events: List[Event] = Field(default_factory=list)
    plot_threads_opened: List[PlotThread] = Field(default_factory=list)
    disguise_alerts: List[DisguiseAlert] = Field(default_factory=list)


@dataclass
class ExtractionReport:
    scene_extractions: list[tuple[scene_splitter.Scene, SceneExtraction]] = field(default_factory=list)
    dropped_hallucinations: list[dict] = field(default_factory=list)
    parse_failures: list[dict] = field(default_factory=list)


def _normalize_for_match(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _quote_found(quote: str, scene_text: str) -> bool:
    if not quote or len(quote) < 8:
        return False
    return _normalize_for_match(quote) in _normalize_for_match(scene_text)


def _is_party_member(name: str) -> bool:
    tokens = re.findall(r"[A-Za-z]+", name.lower())
    return any(t in PARTY_MEMBERS_LOWER for t in tokens)


def _verify_extraction(raw: SceneExtraction, scene_text: str,
                       report: ExtractionReport, scene_index: int) -> SceneExtraction:
    def keep(entities, label, drop_party=False):
        kept = []
        for e in entities:
            quote = getattr(e, "evidence_quote", "")
            if not _quote_found(quote, scene_text):
                name = getattr(e, "name", None) or getattr(e, "summary", None) or getattr(e, "thread", None) or getattr(e, "fake_name", "?")
                report.dropped_hallucinations.append({
                    "scene_index": scene_index, "kind": label,
                    "reason": "evidence_not_found",
                    "name": name, "evidence_quote": quote,
                })
                continue
            if drop_party and _is_party_member(getattr(e, "name", "")):
                report.dropped_hallucinations.append({
                    "scene_index": scene_index, "kind": label,
                    "reason": "party_member_listed_as_npc",
                    "name": e.name, "evidence_quote": quote,
                })
                continue
            kept.append(e)
        return kept

    return SceneExtraction(
        npcs=keep(raw.npcs, "npc", drop_party=True),
        locations=keep(raw.locations, "location"),
        items=keep(raw.items, "item"),
        events=keep(raw.events, "event"),
        plot_threads_opened=keep(raw.plot_threads_opened, "plot_thread"),
        disguise_alerts=keep(raw.disguise_alerts, "disguise_alert"),
    )


def _build_prompts(scene_text: str, prompts_dir: Path) -> tuple[str, str]:
    system = (prompts_dir / "system_constraints.md").read_text(encoding="utf-8")
    task = (prompts_dir / "extract_entities.md").read_text(encoding="utf-8")
    user_prompt = (
        f"{task}\n\n"
        "---\n\nSCENE TEXT:\n\n"
        f"{scene_text}\n\n"
        "---\n\nReturn ONLY the JSON object."
    )
    return system, user_prompt


async def _call_ollama_json(system: str, user: str, model: Optional[str]) -> Optional[str]:
    payload = {
        "model": model or ollama_client.OLLAMA_MODEL,
        "system": system,
        "prompt": user,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(f"{ollama_client.OLLAMA_URL}/api/generate", json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "")
    except Exception as e:
        return f"__ERROR__: {e}"


async def extract_scene(scene: scene_splitter.Scene, prompts_dir: Path,
                        model: Optional[str] = None) -> tuple[Optional[SceneExtraction], Optional[str]]:
    system, user = _build_prompts(scene.text, prompts_dir)
    raw = await _call_ollama_json(system, user, model)
    if raw is None or raw.startswith("__ERROR__"):
        return None, raw or "empty response"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"json decode failed: {e}; head={raw[:200]!r}"
    try:
        return SceneExtraction(**parsed), None
    except ValidationError as e:
        return None, f"pydantic validation failed: {e}"


async def extract_session(scenes: list[scene_splitter.Scene], prompts_dir: Path,
                          model: Optional[str] = None,
                          progress=None) -> ExtractionReport:
    report = ExtractionReport()
    for scene in scenes:
        if progress:
            progress(scene.index, len(scenes), scene.title)
        extracted, err = await extract_scene(scene, prompts_dir, model=model)
        if extracted is None:
            report.parse_failures.append({
                "scene_index": scene.index,
                "title": scene.title,
                "error": err,
            })
            continue
        verified = _verify_extraction(extracted, scene.text, report, scene.index)
        report.scene_extractions.append((scene, verified))
    return report
