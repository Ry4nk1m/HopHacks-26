# Main FastAPI app: builds the app, wires up background jobs, and defines all API routes.

import json
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field

from . import db, features, flags as flagmod, layers, missions, photos, reports, rules, signals, triggers, users
from .config import APP_NAME, SITE_NAME, Settings, load_settings
from .ratelimit import RateLimiter
from . import satellite as satmod
from .tts import TTSUnavailable, synthesize
from .validation import make_validator

STATIC = Path(__file__).parent / "static"
NO_CACHE = {"Cache-Control": "no-cache"}
SYNC_STALE_SECONDS = 300


# Request body for creating a new user.
class UserIn(BaseModel):
    nickname: str = Field(min_length=1, max_length=60)


# Request body for reporting arrival at a mission location.
class ArriveIn(BaseModel):
    lat: float
    lon: float
    accuracy: Optional[float] = None
    manual: bool = False


# Request body for confirming a flag's status.
class ConfirmIn(BaseModel):
    lat: float
    lon: float
    accuracy: Optional[float] = None


# Request body for dev tools to force a trigger condition on or off.
class ForceIn(BaseModel):
    name: str
    mode: str


# Request body for an admin approving or rejecting a pending mission.
class ReviewIn(BaseModel):
    approve: bool
    note: Optional[str] = Field(default=None, max_length=200)


# Request body for a text-to-speech request.
class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=1500)
    lang: str = "en"


# Build and configure the FastAPI app, including routes and background jobs.
def create_app(settings: Optional[Settings] = None, validator=None):
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db_path = settings.data_dir / "app.db"
    validator = validator or make_validator(settings)
    limiter = RateLimiter()
    sync_lock = threading.Lock()
    sat_lock = threading.Lock()
    # the satellite grid can be replaced while the app runs (two-week refresh), so it lives in state, not in a local
    state = {"last_sync": 0.0, "sat": satmod.load_best(settings.snapshot_dir, settings.data_dir),
             "sat_status": {"state": "idle", "at": None, "error": None}}
    render_cache = {}

    # Open a new database connection.
    def conn():
        return db.connect(db_path)

    # Re-check trigger conditions and update flags, but skip if it ran recently (unless forced).
    def sync(force=False):
        with sync_lock:
            if not force and time.monotonic() - state["last_sync"] < SYNC_STALE_SECONDS:
                return None
            with conn() as c:
                result = triggers.sync_flags(c, settings, state["sat"])
            state["last_sync"] = time.monotonic()
            return result

    # Pull in fresh external signals (weather, 311, etc) and then re-sync flags.
    def refresh_external():
        if not settings.external_fetch:
            return {}
        status = signals.refresh_signals(settings, db_path)
        sync(force=True)
        return status

    def refresh_satellite(force=False):
        """Rebuild the satellite snapshot when it is older than the refresh interval (or on demand). Never raises."""
        if not sat_lock.acquire(blocking=False):
            return "busy"
        status = state["sat_status"]
        try:
            if not force and not satmod.is_due(state["sat"], settings.satellite_refresh_days):
                return "not_due"
            status.update(state="running", error=None)
            grid = satmod.refresh(settings, previous=state["sat"])
            state["sat"] = grid
            render_cache.clear()
            status.update(state="ok", at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            sync(force=True)
            return "refreshed"
        except Exception as exc:
            status.update(state="failed", at=datetime.now(timezone.utc).isoformat(timespec="seconds"), error=str(exc)[:300])
            return "failed"
        finally:
            sat_lock.release()

    def satellite_loop():
        time.sleep(300)
        while True:
            refresh_satellite()
            time.sleep(max(settings.satellite_check_hours, 0.05) * 3600)

    # Background thread loop: periodically refresh external data and clean up old photos.
    def background_loop():
        while True:
            time.sleep(settings.signals_refresh_minutes * 60)
            try:
                refresh_external()
                with conn() as c:
                    photos.purge_old_photos(settings, c)
            except Exception:
                pass

    # Runs on app startup and shutdown: loads data, purges old photos, and starts background threads.
    @asynccontextmanager
    async def lifespan(app):
        with conn() as c:
            features.load_snapshot(c, settings)
            photos.purge_old_photos(settings, c)
            first_run = signals.load_signal(c, "city311")[0] is None
        if settings.external_fetch and first_run:
            refresh_external()
        else:
            sync(force=True)
            if settings.external_fetch:
                threading.Thread(target=refresh_external, daemon=True).start()
        if settings.background_jobs:
            threading.Thread(target=background_loop, daemon=True).start()
            if settings.external_fetch and settings.satellite_refresh_days > 0:
                threading.Thread(target=satellite_loop, daemon=True).start()
        yield

    app = FastAPI(title=APP_NAME, docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.settings = settings
    app.state.limiter = limiter
    app.state.sync = sync

    # Turn a MissionError into a JSON error response.
    @app.exception_handler(missions.MissionError)
    async def mission_error(request, exc):
        return JSONResponse({"code": exc.code, **exc.extra}, status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        # one JSON shape for every API error: {"code": ...} (framework errors keep {"detail": ...})
        body = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
        return JSONResponse(body, status_code=exc.status_code)

    # Turn a UserError into a JSON error response.
    @app.exception_handler(users.UserError)
    async def user_error(request, exc):
        return JSONResponse({"code": exc.code}, status_code=exc.status)

    # ------------------------------------------------------------ auth
    # Look up the logged-in user from the bearer token, or fail if not logged in.
    def current_user(authorization: Optional[str] = Header(None)):
        token = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
        with conn() as c:
            u = users.user_by_token(c, token)
        if u is None:
            raise HTTPException(401, detail={"code": "unauthorized"})
        return u

    # Look up the user from the bearer token if there is one, but allow anonymous access.
    def optional_user(authorization: Optional[str] = Header(None)):
        token = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
        with conn() as c:
            return users.user_by_token(c, token)

    # Block access unless dev tools are enabled in settings.
    def require_dev():
        if not settings.dev_tools:
            raise HTTPException(404, detail="not found")

    # Block access unless the correct admin key was supplied.
    def require_admin(x_admin_key: Optional[str] = Header(None), key: Optional[str] = None):
        supplied = x_admin_key or key
        if not settings.admin_key or supplied != settings.admin_key:
            raise HTTPException(403, detail={"code": "forbidden"})

    # Raise an error if this key has exceeded its allowed count within the time window.
    def limit(key, count, window):
        if not limiter.allow(key, count, window):
            raise HTTPException(429, detail={"code": "rate_limited"})

    # Get the caller's IP address, checking the forwarded-for header first.
    def client_ip(request: Request):
        fwd = request.headers.get("x-forwarded-for")
        return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?"))

    # Read an uploaded file's bytes, rejecting it if it is over the max upload size.
    def read_upload(upload: UploadFile):
        data = upload.file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, detail={"code": "too_large"})
        return data

    # ------------------------------------------------------------ public
    # Simple health check endpoint.
    @app.get("/api/health")
    def health():
        return {"ok": True}

    # Return app config: names, feature flags, area of interest, rules, and layer info.
    @app.get("/api/config")
    def get_config():
        satellite = state["sat"]
        meta = satellite.meta if satellite else None
        return {
            "app_name": APP_NAME, "site_name": SITE_NAME, "validator": validator.name, "tts": settings.tts_enabled, "dev_tools": settings.dev_tools,
            "aoi": list(settings.aoi), "center": list(settings.center), "map_style_url": settings.map_style_url,
            "photo_retention_days": settings.photo_retention_days,
            "rules": {"flag_types": {k: {"points": v["points"], "radius_m": v["radius_m"], "purpose": v["purpose"]} for k, v in rules.FLAG_TYPES.items()},
                      "manual_arrival_max_m": rules.MANUAL_ARRIVAL_MAX_M, "accuracy_cap_m": rules.ACCURACY_ALLOWANCE_CAP_M,
                      "max_attempts": rules.MAX_ATTEMPTS, "max_active_missions": rules.MAX_ACTIVE_MISSIONS, "confirm_radius_m": rules.CONFIRM_RADIUS_M,
                      "claim_minutes": rules.CLAIM_MINUTES, "badges": list(rules.BADGES)},
            "layers": {
                "bounds": layers.bounds_of(satellite) if satellite else None,
                "ndvi": {"scenes": meta["ndvi"]["scenes"], "source": meta["ndvi"]["source"]} if meta else None,
                "lst": {"scenes": meta["lst"]["scenes"], "source": meta["lst"]["source"]} if meta else None,
                "legends": layers.LEGENDS},
        }

    # Serve a rendered map layer PNG (ndvi or lst), rendering it once and caching it.
    @app.get("/api/layers/{name}.png")
    def layer_png(name: str):
        satellite = state["sat"]
        if satellite is None or name not in ("ndvi", "lst"):
            raise HTTPException(404, detail="not found")
        if name not in render_cache:
            render_cache[name] = layers.render_png(satellite, name)
        return Response(render_cache[name], media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})

    # Create a new user account and return it with an auth token.
    @app.post("/api/users", status_code=201)
    def create_user(body: UserIn, request: Request):
        limit(("signup", client_ip(request)), 20, 3600)
        with conn() as c:
            u = users.create_user(c, body.nickname, "en")
            return users.public_user(c, u, include_token=True)

    # Get the logged-in user's profile, streak, and active missions.
    @app.get("/api/me")
    def me(user=Depends(current_user)):
        now = datetime.now(timezone.utc)
        with conn() as c:
            out = users.public_user(c, user)
            out["streak"] = users.current_streak(c, user["id"], now, settings.timezone)
            out["active_missions"] = missions.active_missions(c, user["id"], now)
            return out

    # List all flags on the map along with current weather/heat/rain conditions.
    @app.get("/api/flags")
    def list_flags(user=Depends(optional_user)):
        sync()
        now = datetime.now(timezone.utc)
        with conn() as c:
            items = flagmod.list_flags(c, user, now, settings.timezone)
            cond = signals.conditions(c)
            _, weather_at = signals.load_signal(c, "weather")
            status, _ = signals.load_signal(c, "status")
        return {"flags": items, "conditions": {
            "heat": cond["heat"], "rain": cond["rain"], "dry": cond["dry"], "forced": cond["forced"], "auto": cond["auto"],
            "metrics": cond["metrics"], "events": [e["event"] for e in cond["events"]], "weather_at": weather_at, "status": status}}

    # Get details for a single flag by id.
    @app.get("/api/flags/{flag_id}")
    def get_flag(flag_id: int, user=Depends(optional_user)):
        with conn() as c:
            row = c.execute("SELECT * FROM flags WHERE id=?", (flag_id,)).fetchone()
            if row is None:
                raise HTTPException(404, detail={"code": "not_found"})
            return flagmod.flag_to_dict(c, row, user, datetime.now(timezone.utc), settings.timezone)

    # Get the points leaderboard.
    @app.get("/api/leaderboard")
    def leaderboard():
        with conn() as c:
            return users.leaderboard(c)

    # Get overall community impact stats.
    @app.get("/api/impact")
    def impact():
        with conn() as c:
            return users.impact(c)

    # ------------------------------------------------------------ missions
    # Let the logged-in user accept a flag and start a mission for it.
    @app.post("/api/flags/{flag_id}/accept")
    def accept_mission(flag_id: int, user=Depends(current_user)):
        with conn() as c:
            m = missions.accept(c, user, flag_id, datetime.now(timezone.utc))
        return {"mission": m}

    # Record the user's arrival location for a mission.
    @app.post("/api/missions/{mission_id}/arrive")
    def arrive(mission_id: int, body: ArriveIn, user=Depends(current_user)):
        with conn() as c:
            return missions.arrive(c, user, mission_id, body.lat, body.lon, body.accuracy, body.manual, datetime.now(timezone.utc))

    # Cancel an in-progress mission for the logged-in user.
    @app.post("/api/missions/{mission_id}/cancel")
    def cancel(mission_id: int, user=Depends(current_user)):
        with conn() as c:
            return {"mission": missions.cancel(c, user, mission_id, datetime.now(timezone.utc))}

    # Submit photo(s) to complete or confirm a mission, rate limited per user.
    @app.post("/api/missions/{mission_id}/submit")
    def submit(mission_id: int, photo: UploadFile = File(...), before: Optional[UploadFile] = File(None), lat: float = Form(...), lon: float = Form(...),
               accuracy: Optional[float] = Form(None), was_problem: Optional[str] = Form(None), lang: str = Form("en"), user=Depends(current_user)):
        limit(("submit", user["id"]), 8, 600)
        data = read_upload(photo)
        before_data = read_upload(before) if before is not None else None
        result = missions.submit(settings, validator, user["id"], mission_id, data, before_data or None, lat, lon, accuracy, was_problem, lang)
        if result.get("outcome") in ("verified", "pending"):
            sync(force=True)
        return result

    # Create a new user-reported issue (a report) with a photo, rate limited per user.
    @app.post("/api/reports")
    def create_report(photo: UploadFile = File(...), type: str = Form(...), note: Optional[str] = Form(None), lat: float = Form(...), lon: float = Form(...),
                      accuracy: Optional[float] = Form(None), lang: str = Form("en"), user=Depends(current_user)):
        limit(("report", user["id"]), 6, 3600)
        data = read_upload(photo)
        result = reports.create_report(settings, validator, user["id"], data, type, note, lat, lon, accuracy, lang)
        return result

    # Confirm whether a flag's problem is still present, based on the user's location.
    @app.post("/api/flags/{flag_id}/confirm")
    def confirm(flag_id: int, body: ConfirmIn, user=Depends(current_user)):
        with conn() as c:
            return reports.confirm_flag(c, user, flag_id, body.lat, body.lon, body.accuracy, datetime.now(timezone.utc), settings)

    # ------------------------------------------------------------ voice
    # Turn text into speech audio (mp3), falling back to browser TTS if unavailable.
    @app.post("/api/tts")
    def tts(body: TTSIn):
        try:
            audio = synthesize(settings, body.text, body.lang if body.lang in rules.LANGS else "en")
        except TTSUnavailable as exc:
            return JSONResponse({"detail": str(exc), "fallback": "browser"}, status_code=503)
        return Response(audio, media_type="audio/mpeg")

    # ------------------------------------------------------------ dev tools (demo and walk-testing)
    # Show current trigger conditions and flag counts, for debugging (dev tools only).
    @app.get("/api/dev/state", dependencies=[Depends(require_dev)])
    def dev_state():
        with conn() as c:
            cond = signals.conditions(c)
            counts = {r["type"] + ":" + r["status"]: r["c"] for r in c.execute("SELECT type, status, COUNT(*) c FROM flags GROUP BY type, status")}
            return {"conditions": {k: cond[k] for k in ("heat", "rain", "dry", "auto", "forced")}, "flags": counts,
                    "status": signals.load_signal(c, "status")[0], "validator": validator.name,
                    "satellite": {**state["sat_status"], "snapshot": state["sat"].meta["generated_at"] if state["sat"] else None,
                                  "refresh_days": settings.satellite_refresh_days}}

    # Force a trigger condition on or off for testing (dev tools only).
    @app.post("/api/dev/force", dependencies=[Depends(require_dev)])
    def dev_force(body: ForceIn):
        try:
            with conn() as c:
                signals.set_forced(c, body.name, body.mode)
        except ValueError:
            raise HTTPException(422, detail={"code": "bad_trigger"})
        return {"sync": sync(force=True)}

    # Force a refresh of external signal data (dev tools only).
    @app.post("/api/dev/refresh", dependencies=[Depends(require_dev)])
    def dev_refresh():
        return {"status": refresh_external(), "sync": sync(force=True)}

    @app.post("/api/dev/satellite", dependencies=[Depends(require_dev)])
    def dev_satellite():
        if state["sat_status"]["state"] == "running":
            return {"started": False, "reason": "already_running"}
        threading.Thread(target=refresh_satellite, kwargs={"force": True}, daemon=True).start()
        return {"started": True}

    # Wipe missions, reports, and user progress back to a clean demo state (dev tools only).
    @app.post("/api/dev/reset", dependencies=[Depends(require_dev)])
    def dev_reset():
        with conn() as c:
            for table in ("missions", "confirmations", "photo_hashes", "point_events", "badges"):
                c.execute(f"DELETE FROM {table}")
            c.execute("DELETE FROM flags WHERE source='user' OR status IN ('resolved','expired','pending')")
            c.execute("UPDATE flags SET claimed_by=NULL, claim_expires_at=NULL, verified_count=0")
            c.execute("UPDATE features SET last_verified_at=NULL, info=NULL")
            c.execute("UPDATE users SET points=0, streak=0, best_streak=0, last_active_day=NULL")
            c.execute("DELETE FROM signals WHERE key='forced'")
        return {"sync": sync(force=True)}

    # ------------------------------------------------------------ admin review queue
    # List missions waiting for admin review.
    @app.get("/api/admin/review", dependencies=[Depends(require_admin)])
    def admin_review():
        with conn() as c:
            return {"pending": missions.pending_reviews(c)}

    # Approve or reject a pending mission (admin only).
    @app.post("/api/admin/review/{mission_id}", dependencies=[Depends(require_admin)])
    def admin_decide(mission_id: int, body: ReviewIn):
        with conn() as c:
            return missions.review_decide(c, settings, mission_id, body.approve, body.note, datetime.now(timezone.utc))

    # Serve a mission's before/after photo file to an admin.
    @app.get("/api/admin/photo/{mission_id}", dependencies=[Depends(require_admin)])
    def admin_photo(mission_id: int, which: str = "after"):
        with conn() as c:
            r = c.execute("SELECT photo_path, before_path FROM missions WHERE id=?", (mission_id,)).fetchone()
        rel = r["before_path" if which == "before" else "photo_path"] if r else None
        path = (settings.data_dir / rel).resolve() if rel else None
        if not path or not path.is_file() or settings.data_dir.resolve() not in path.parents:
            raise HTTPException(404, detail="not found")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------ static
    # Serve the main app page.
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers=NO_CACHE)

    # Serve the admin review page.
    @app.get("/admin")
    def admin_page():
        return FileResponse(STATIC / "admin.html", headers=NO_CACHE)

    # Serve the web app manifest file.
    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json", headers=NO_CACHE)

    # Serve a static file, blocking access outside the static folder.
    @app.get("/static/{path:path}")
    def static_file(path: str):
        target = (STATIC / path).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            raise HTTPException(404, detail="not found")
        headers = {"Cache-Control": "public, max-age=86400"} if ("/vendor/" in "/" + path or path.endswith((".gif", ".png", ".webp", ".jpg", ".woff2"))) else NO_CACHE
        return FileResponse(target, headers=headers)

    return app


# Module-level app instance used by the ASGI server (uvicorn) to run the app.
app = create_app()
