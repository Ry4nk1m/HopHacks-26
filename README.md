# Parasol

By TheUmbrellaClub. Small neighborhood tasks, guided by satellite data.

A mobile-first map app where **satellites and city data show where small tasks would help**, volunteers do them, and
**Gemini checks the photo**. Covers all of Baltimore City (the area is configurable).

Open the app, see flags on a map, tap one, walk there, do the task, take a photo, and earn points. Flags are
tasks like *water this tree during a dry spell*, *clear this storm drain before heavy rain*, *verify a cooling
center's hours*, or *check whether a reported flooded street or dumping is still there*.

## How the three data layers fit together

| Layer | Source | Refreshes | Answers |
|---|---|---|---|
| **Where** | Sentinel-2 vegetation and Landsat 9 surface heat (about 30 m), read from Microsoft Planetary Computer; OpenStreetMap trees, drains and public spaces; Baltimore Code Red cooling centers | when you rebuild the snapshots | which trees and spots matter most |
| **When** | Open-Meteo forecast and National Weather Service alerts | every 30 minutes | heat, heavy rain, dry spell triggers |
| **What is really happening** | Baltimore 311 requests (live) and volunteer photos | live | is the problem real, is it still there |

Satellites are not real time at street level, so they only rank *where to look*. Weather sets *when*, and volunteers'
photos are the up-to-date ground truth. Every completed mission also records whether the flag was a real problem, which
lets `eval/eval_satellite.py` measure whether the satellite ranking actually predicts real need.

## Trust and safety design

- **Gemini validates every photo** for the specific task, and refuses photos with people or private information.
  Photo text is treated as data, not instructions (tested against an "ignore all rules" note).
- **Confident** results are verified; **borderline** ones go to a human review queue (`/admin`); low-confidence ones are rejected with a retry (3 tries).
- **Anti-abuse:** GPS arrival check (accuracy-aware, with a stricter manual override), exact and near-duplicate photo detection, per-user rate limits, a daily points cap, claim locks so two people don't double up, and second confirmation for user-created reports.
- **Privacy:** photos are re-encoded (removes EXIF location), kept only for review, and deleted after `PHOTO_RETENTION_DAYS`. Accounts are just nicknames.
- Every satellite statement in the UI says it describes the surrounding area, not the exact spot.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # add keys; set DEV_TOOLS=1 for demos and walk tests
.venv/bin/uvicorn app.main:app --port 8124
```

Open http://localhost:8124. With no `GEMINI_API_KEY` the app runs in a clearly labelled **demo mode** where photo checks
are simulated.

### Demo tools (`DEV_TOOLS=1`)
In *Profile*: teleport next to any flag (or long-press the map), force heat / heavy rain / dry-spell conditions, refresh live data, and reset.
Real users are never affected by these tools; leave `DEV_TOOLS=0` on a public deployment.

### Walking test on your phone
The camera and GPS need HTTPS. The quickest way is a tunnel to your laptop:

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8124
```

Open the printed `https://...trycloudflare.com` address on your phone. See `docs/WALK_TEST.md` for a checklist.

### Settings

| Variable | Effect |
|---|---|
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Real photo checks (default model `gemini-3.1-flash-lite`; change it if Google retires it). |
| `GEMINI_FALLBACK_MODELS` | Backup models tried in order if the main one is out of quota or overloaded. Each model has its own free allowance, so a chain keeps checks working on the free tier. |
| `FORCE_MOCK_VALIDATOR=1` | Simulated photo checks even when a key is present (useful for UI testing). |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | Spoken mission briefings (falls back to the browser voice). The voice must be one your plan can use through the API. |
| `DEV_TOOLS` | Demo tools on or off. |
| `ADMIN_KEY` | Enables the review queue at `/admin` (empty = disabled). |
| `AOI_BBOX` | Different area, as `west,south,east,north`. Then rebuild both snapshots (below). |
| `MAP_STYLE_URL` | Any MapLibre style (default OpenFreeMap, no key needed). |
| `PHOTO_RETENTION_DAYS`, `DATA_DIR` | Retention window and storage location. |

## Rebuilding the data snapshots

```bash
.venv/bin/pip install -r requirements-data.txt
.venv/bin/python scripts/build_features.py      # trees, drains, public spaces, cooling centers
.venv/bin/python scripts/build_satellite.py     # newest clear Sentinel-2 and Landsat scenes
```

## Tests and evaluation

```bash
.venv/bin/python -m pytest -q                              # 69 tests, no network or keys needed
.venv/bin/python -m eval.eval_validation --dir eval/photos # accuracy of the photo checker on YOUR labelled photos
.venv/bin/python -m eval.eval_satellite                    # does satellite priority predict real problems?
```

`eval/photos/README.md` explains the folder layout for your photos. Do not quote accuracy numbers until you have run these
on real photos taken in the field.

## Deploy

- `Dockerfile` (built and run in a container; non-root; accounts persist across restarts when `/srv/data` is a volume) and `.do/app.yaml` for DigitalOcean App Platform (storage is temporary there; fine for a demo).
- `deploy/docker-compose.yml` runs the app with Caddy (automatic HTTPS) and a persistent volume on a small droplet. The file validates with `docker compose config`; the Caddy and HTTPS part has not been run against a real domain.

## Known limits

- Only Baltimore City has bundled data; other areas need the two build scripts.
- Satellite imagery is days to weeks old and about 30 m per pixel; clouds mean some scenes are skipped.
- OpenStreetMap has good tree coverage here but only a handful of mapped storm drains, so most drain flags come from 311 reports or heavy-rain triggers.
- A volunteer can spoof GPS. Arrival checks and photo checks reduce abuse but do not prove presence.
- Free Gemini keys have low rate limits; the app retries and reports "checker unavailable" without costing the volunteer a try.
- Reporter notes are free text shown to other volunteers; there is no profanity filter.
- Web apps cannot run in the background or send push notifications on most phones.

## Layout

```
app/            FastAPI backend (rules, triggers, missions, reports, validation) and the static frontend (app/static)
app/data/       bundled snapshots: features.json (OSM and city data), satellite.json (NDVI and surface heat)
scripts/        rebuild the snapshots
eval/           accuracy and satellite-value measurements
tests/          69 tests
deploy/         docker-compose and Caddy for a small server
```

## Satellite refresh

The app ships with a satellite snapshot (`app/data/satellite.json`). While running, it checks every 6 hours and, once the snapshot is
14 days old, rebuilds it from the newest low-cloud Sentinel-2 and Landsat scenes on Microsoft Planetary Computer. The build runs in a
separate process (about 20 seconds, under 200 MB for the whole city), is checked before it replaces the old snapshot, and the old one keeps serving if anything
fails. The new file lives in the data directory, so on a host with temporary storage it is lost on redeploy and the bundled snapshot is used
again until the next refresh. Set `SATELLITE_REFRESH_DAYS=0` to turn this off. In dev mode, `POST /api/dev/satellite` runs it now.
