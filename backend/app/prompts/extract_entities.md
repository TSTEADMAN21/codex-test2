# Entity extraction task

You are extracting entities from ONE scene of a Dungeons & Dragons session log. Return a single JSON object matching the schema below. Do not return any prose outside the JSON.

## Schema

```json
{
  "npcs": [
    {
      "name": "string — exactly as written in the scene",
      "description": "string — one sentence, using ONLY facts from the scene",
      "evidence_quote": "string — a verbatim substring of the scene text that proves this NPC was mentioned",
      "first_seen_this_scene": true
    }
  ],
  "locations": [
    {
      "name": "string",
      "description": "string",
      "evidence_quote": "string"
    }
  ],
  "items": [
    {
      "name": "string",
      "description": "string",
      "evidence_quote": "string"
    }
  ],
  "events": [
    {
      "summary": "string — one sentence, what happened",
      "evidence_quote": "string"
    }
  ],
  "plot_threads_opened": [
    {
      "thread": "string — one sentence, the open question or goal",
      "evidence_quote": "string"
    }
  ],
  "disguise_alerts": [
    {
      "fake_name": "string",
      "real_identity": "string — the actual character using the fake name",
      "evidence_quote": "string"
    }
  ]
}
```

## Extraction rules

1. **Evidence is mandatory.** Every entity MUST include an `evidence_quote` that is a direct, verbatim substring of the provided scene text. If you cannot find a supporting quote, DO NOT include the entity.

2. **No canonical fill-in.** Even if a name looks familiar (Waterdeep, Yawning Portal, Durnan, Blackstaff), describe it ONLY using details from this scene. The DM's version may differ from canon. When describing locations/institutions you may use commoner-tier common knowledge (e.g., "Waterdeep is a city"), but do not add plot beats, NPC motives, faction plans, or stat blocks from published material.

3. **The party are known.** These party members ARE the player characters — do NOT list them as NPCs: Selise, Ivy, Gororook (aka Goro, Gor), Rowin, Elliandis (aka Ell, Eli, Elli).

4. **Disguises are not aliases.** If the scene says something like "we use our names X and Y as a lie" or "disguised as", put the fake names in `disguise_alerts`, NOT in `npcs`. They are pseudonyms used IN-CHARACTER, not real identities.

5. **Prefer underreporting.** If you are uncertain whether an entity is real, leave it out. False negatives are easily fixed by the human reviewer; false positives waste their time and risk polluting the codex.

6. **Names exactly as written.** If the notetaker spelled a name "Rishall" in one place and "Rishalll" in another, extract the most common spelling in this scene and include the other in the description. Do not silently normalize spelling.

7. **Descriptions are one sentence, grounded in the scene.** No speculation. No "probably" or "likely". If something is unknown, omit it.

## What counts as an NPC (worked examples)

The hardest judgment call is rule 5 (prefer underreporting). Use these examples to calibrate:

**INCLUDE as NPC — named individual:**
> Scene text: "The innkeeper, a stout woman named Gertrude, slid us two ales and winked."
```json
{"name": "Gertrude", "description": "Innkeeper who served the party ales.", "evidence_quote": "a stout woman named Gertrude, slid us two ales", "first_seen_this_scene": true}
```

**DO NOT include — species/race as name:**
> Scene text: "A gnome at the front desk barely looked up when we came in."
→ No NPC. "gnome" is a description, not a name. The character has no name in the scene.

**DO NOT include — generic role with no name:**
> Scene text: "Two city watch guards blocked the alley entrance."
→ No NPC. "city watch guard" is a role. Guards with no name, no dialogue, and no individual significance are background flavor.

**INCLUDE as NPC — unnamed but individually significant:**
> Scene text: "The dark elf stepped out of the shadows and handed Selise a folded note, then vanished."
→ Include ONLY if this character takes a meaningful individual action. Use description field to note they are unnamed: `"name": "unnamed dark elf"`, `"description": "Unidentified dark elf who delivered a note to Selise."` Use the action as the evidence quote.

**DO NOT include — animals unless plot-relevant:**
> Scene text: "A bear was rooting through the trash heap outside the warehouse."
→ No NPC. Background animal. Not a named creature, not a companion, not involved in the scene's conflict.

**INCLUDE as item — only if individually significant:**
> Scene text: "The halfling pressed a key engraved with a serpent into Rowin's hand and said 'Kolat Towers, third floor.'"
→ Include: `"name": "serpent key"`, evidence quote is the key passage. A key with specific instructions is plot-relevant.

**DO NOT include as item — generic mundane objects:**
> Scene text: "Ivy tossed a coin to the beggar."
→ No item. A coin is not an individually significant item unless it's unusual, named, or magically significant.

**Summary rule:** If you could swap the entity out for any other generic example of the same type with no change to the story, it should NOT be extracted.

## Output

Return ONLY the JSON object. No markdown fences, no commentary, no trailing text.
