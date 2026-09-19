# Devpost draft

**Tagline:** Satellites see where. Neighbors fix it. Gemini checks the photo.

**Inspiration:** Cities know about far more small problems than they can inspect: dry street trees in a heat wave, clogged
storm drains before a storm, cooling centers with wrong hours. Volunteers are willing, but there is no way to point them at the
right spot at the right time, or to trust the result.

**What it does:** A map of flags built from real data. Sentinel-2 and Landsat imagery rank where trees are least green and hottest;
live weather decides when a flag matters; Baltimore 311 and cooling-center data supply real problems. A volunteer walks to a flag,
does a short task, and photographs it. Gemini verifies the photo for that exact task, refuses images with people or private
information, and routes borderline cases to a human review queue. Points, streaks and badges keep it fun; spoken briefings read each task aloud.

**How we built it:** FastAPI + SQLite, MapLibre GL with OpenFreeMap, Gemini vision for verification, ElevenLabs for voice briefings,
Microsoft Planetary Computer for satellite reads, Open-Meteo and NWS for weather, and city open data. Deployed on DigitalOcean.

**Challenges:** Being honest about what satellites can and cannot see, making photo verification robust to bad or hostile input,
and designing anti-abuse that does not punish honest volunteers when GPS drifts or the AI is briefly unavailable.

**Results:** _Fill in from `eval_validation` and `eval_satellite` on real field photos._

**What's next:** More neighborhoods, a city-facing dashboard of verified work, push notifications, and offline capture.

**Prize tracks:** Bloomberg Most Philanthropic Hack, Best Overall, MLH Best Use of Gemini API, ElevenLabs, MLH Best Use of DigitalOcean.
