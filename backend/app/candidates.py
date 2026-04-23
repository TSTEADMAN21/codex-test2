"""Identify candidate entities (NPCs, locations, items) from session text.

This is intentionally *heuristic*, not LLM-powered — the extraction step runs
for free on any hardware and produces a candidate list the user reviews before
promoting anything into the canonical codex. This preserves the anti-spoiler
constraint: no automatic name invention or lore fill-in.
"""
from __future__ import annotations
import re
from collections import Counter
from dataclasses import dataclass, field

PARTY_MEMBERS = {
    "Selise", "Ivy", "Gororook", "Rowin", "Elliandis",
}
PARTY_ALIASES = {
    "Goro": "Gororook", "Gor": "Gororook", "Bororook": "Gororook",
    "Ell": "Elliandis", "Eli": "Elliandis", "Elli": "Elliandis",
    "Rose": "Ivy",
}

DISGUISE_CUES = re.compile(
    r"(?i)(?:use\s+(?:our|their|her|his)\s+names?|as\s+a\s+lie|disguised\s+as|pseudonym|fake\s+name)"
)

CAPITALIZED_NAME_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]+)?)\b")

LOCATION_HINT_WORDS = {
    "ward", "tower", "manor", "shop", "portal", "tavern", "library",
    "statue", "alley", "dock", "bridge", "castle", "temple", "inn",
    "market", "street", "square",
}

ITEM_HINT_WORDS = {
    "potion", "scroll", "sword", "dagger", "bow", "pistol", "firearm",
    "boots", "cloak", "ring", "amulet", "necklace", "tome", "book",
    "oil", "powder",
}

STOPWORDS = {
    "The", "And", "But", "Her", "His", "She", "His", "They", "We",
    "That", "This", "There", "When", "Where", "Then", "After", "Before",
    "With", "From", "Into", "Onto", "Out", "Also", "Just", "Still",
    "Ivy", "Selise", "Rowin", "Gororook", "Elliandis", "Mid",
    "Only", "Lots", "Tons", "Asks", "Says",
}


@dataclass
class CandidateSet:
    npcs: dict[str, int] = field(default_factory=dict)
    locations: dict[str, int] = field(default_factory=dict)
    items: dict[str, int] = field(default_factory=dict)
    disguise_flags: list[str] = field(default_factory=list)


def extract_candidates(text: str) -> CandidateSet:
    cand = CandidateSet()

    for m in DISGUISE_CUES.finditer(text):
        start = max(0, m.start() - 60)
        end = min(len(text), m.end() + 80)
        cand.disguise_flags.append(text[start:end].replace("\n", " ").strip())

    counter: Counter[str] = Counter()
    for m in CAPITALIZED_NAME_RE.finditer(text):
        name = m.group(1).strip()
        if name in STOPWORDS or name in PARTY_MEMBERS or name in PARTY_ALIASES:
            continue
        if len(name) < 4:
            continue
        counter[name] += 1

    for name, count in counter.items():
        window = _context_window(text, name)
        lower = window.lower()
        if any(w in lower for w in LOCATION_HINT_WORDS):
            cand.locations[name] = count
        elif any(w in lower for w in ITEM_HINT_WORDS):
            cand.items[name] = count
        else:
            cand.npcs[name] = count

    return cand


def _context_window(text: str, name: str, radius: int = 40) -> str:
    idx = text.find(name)
    if idx < 0:
        return ""
    return text[max(0, idx - radius): idx + len(name) + radius]
