# 3-minute demo script

Setup: two phones (or a phone and a laptop), Gemini and ElevenLabs keys set, `DEV_TOOLS=1`, and a backup screen recording.

**0:00 The problem (20s)**
"Cities have long lists of small problems that nobody can inspect: dry street trees, clogged drains, unverified cooling
centers. Satellites can see where; volunteers can fix; but nobody checks the work."

**0:20 Where and when (40s)**
Open the map. Toggle the *Surface heat* layer: "Landsat 9, 30 m, from last week". Force *Dry spell: On*: 60 flags appear,
chosen where the satellite says trees are least green and hottest. "Satellites say where. Weather says when."

**1:00 A mission (60s)**
Tap a flag: show the reasons, satellite facts, and the real 311 or OpenStreetMap data behind it. Accept (the voice
briefing plays). Teleport next to it, take the photo. Gemini's verdict appears with
its reasoning. Points, streak, badge.

**2:00 Trust (40s)**
Submit a photo of your shoe: rejected. Submit one with a face in it: rejected for privacy. Show a borderline photo in
`/admin`. "Confident results auto-verify, doubtful ones go to a person."

**2:40 Evidence and close (20s)**
Slide: results from `eval_validation` and `eval_satellite` on real photos (only numbers you actually measured).
"Volunteers' answers tell us whether the satellite ranking predicts real need."

## Likely questions
- **Can people fake it?** GPS and photos can be faked; we check arrival distance, duplicates, rate limits, privacy, and route doubtful cases to review. It is a prototype, not proof of presence.
- **Why satellites if they are so coarse?** No one can inspect every tree or drain; satellites rank where to look first. We state the resolution limit in the UI and measure whether the ranking predicts real need.
- **What does the city get?** A verified, timestamped record of what was fixed and what was still broken, plus crowd-checked cooling center hours.
