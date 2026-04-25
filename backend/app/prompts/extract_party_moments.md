# Party moments extraction task

You are reading ONE scene from a Dungeons & Dragons session log. Your job is to identify any **significant actions** taken by the player characters (party members) in this scene.

The party members are: **Selise, Ivy, Gororook (aka Goro, Gor), Rowin, Elliandis (aka Ell, Eli, Elli)**.

Return a single JSON object. Do not return any prose outside the JSON.

## Schema

```json
{
  "moments": [
    {
      "character": "string — exact party member name as you know them (e.g. 'Gororook', not 'Gor')",
      "moment": "string — one sentence describing what this character did that was significant",
      "evidence_quote": "string — a verbatim substring of the scene text that proves this happened",
      "scene": 0
    }
  ]
}
```

## What counts as a significant moment

Extract a moment when a party member:
- Makes a **major decision** that could affect the story (agrees to a job, refuses a deal, swears an oath)
- Has a **personal revelation** or discovers something important about themselves or their past
- **Forms or breaks a bond** with an NPC (befriends, betrays, or angers a recurring character)
- Takes a **bold or risky action** that stands out (attacks first, sacrifices something, uses a rare ability in a clutch moment)
- **Acquires or loses** something meaningful (a significant item, a title, a relationship)
- Is **targeted specifically** by an enemy or NPC in a way that affects them personally

## What does NOT count

- Routine combat actions ("Ivy attacks the goblin")
- Generic movement ("Gororook walks into the tavern")
- Background description where the character is mentioned but does nothing notable
- Actions taken by the group together with no individual character standing out

## Rules

1. **Evidence is mandatory.** Every moment MUST have an `evidence_quote` that is a direct verbatim substring of the scene text. If you cannot find a supporting quote, DO NOT include the moment.
2. **One moment per character per scene maximum.** Pick the most significant action only.
3. **Only party members.** Do not create moments for NPCs.
4. **No canonical fill-in.** Only use facts from this scene text.
5. **Prefer underreporting.** If nothing significant happened for a character in this scene, do not force one. An empty `moments` array is a valid and expected result.

## Output

Return ONLY the JSON object. No markdown fences, no commentary.
