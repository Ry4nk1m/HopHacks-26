"""Rebuild the bundled satellite snapshot in app/data/satellite.json.

    .venv/bin/python scripts/build_satellite.py

The running app also refreshes its own copy every two weeks (see app/satellite.py); this script is for updating what ships in the repo.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.satellite_build import build  # noqa: E402

if __name__ == "__main__":
    build()
