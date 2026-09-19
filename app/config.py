# App settings and config loading.
# Reads values from the .env file and system env vars, then builds a Settings object.

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "Parasol"
SITE_NAME = "TheUmbrellaClub"

# Johns Hopkins Homewood area (west, south, east, north)
DEFAULT_AOI = (-76.645, 39.310, -76.595, 39.350)


# Read key=value lines from a .env file and load them into the environment.
# Skips blank lines and comments. Does not overwrite vars already set.
def _load_dotenv(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


# Read an env var as a bool. Accepts 1/true/yes/on as true.
def _bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Holds all app settings, filled in by load_settings below.
@dataclass
class Settings:
    data_dir: Path
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    eleven_api_key: str = ""
    eleven_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    dev_tools: bool = False
    admin_key: str = ""
    force_mock_validator: bool = False
    map_style_url: str = "https://tiles.openfreemap.org/styles/liberty"
    aoi: tuple = DEFAULT_AOI
    timezone: str = "America/New_York"
    photo_retention_days: int = 14
    max_upload_bytes: int = 12 * 1024 * 1024
    signals_refresh_minutes: int = 30
    satellite_refresh_days: int = 14
    satellite_check_hours: float = 6.0
    background_jobs: bool = True
    external_fetch: bool = True
    snapshot_dir: Path = field(default_factory=lambda: ROOT / "app" / "data")

    # Use gemini for validation if we have a key and mock mode is off.
    @property
    def validator_mode(self):
        return "gemini" if self.gemini_api_key and not self.force_mock_validator else "mock"

    # Text to speech is only on if we have an ElevenLabs key.
    @property
    def tts_enabled(self):
        return bool(self.eleven_api_key)

    # Midpoint of the area of interest, as (lat, lon).
    @property
    def center(self):
        w, s, e, n = self.aoi
        return ((s + n) / 2, (w + e) / 2)


# Build a Settings object from env vars, with fallback defaults.
def load_settings():
    # Data dir can be relative or absolute. Relative paths are from the project root.
    data_dir = Path(os.environ.get("DATA_DIR", ROOT / "data"))
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir

    # Parse the area of interest bounding box if one was set.
    aoi = DEFAULT_AOI
    raw = os.environ.get("AOI_BBOX", "").strip()
    if raw:
        parts = [float(p) for p in raw.split(",")]
        if len(parts) == 4:
            aoi = tuple(parts)

    return Settings(
        data_dir=data_dir,
        gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash").strip(),
        eleven_api_key=os.environ.get("ELEVENLABS_API_KEY", "").strip(),
        eleven_voice_id=os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM").strip(),
        dev_tools=_bool("DEV_TOOLS", False),
        admin_key=os.environ.get("ADMIN_KEY", "").strip(),
        force_mock_validator=_bool("FORCE_MOCK_VALIDATOR", False),
        map_style_url=os.environ.get("MAP_STYLE_URL", "https://tiles.openfreemap.org/styles/liberty").strip(),
        aoi=aoi,
        photo_retention_days=int(os.environ.get("PHOTO_RETENTION_DAYS", "14")),
        satellite_refresh_days=int(os.environ.get("SATELLITE_REFRESH_DAYS", "14")),
    )
