"""Dashboard configuration loaded from environment and azimuth paths."""

from __future__ import annotations

import os
from pathlib import Path

AZIMUTH_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_ROOT = Path(__file__).resolve().parent
DATA_DIR = DASHBOARD_ROOT / "data"
EXPORT_DIR = Path(os.getenv("GARMIN_EXPORT_DIR", AZIMUTH_ROOT / "export"))
CACHE_DIR = EXPORT_DIR / ".cache"
GARMIN_EXPORT_SCRIPT = AZIMUTH_ROOT / "garmin_export.py"

HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.getenv("DASHBOARD_PORT", "8765"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

STATE_FILE = DATA_DIR / "user_state.json"


def load_env() -> None:
    """Load azimuth/.env into os.environ (non-destructive)."""
    for env_path in (AZIMUTH_ROOT / ".env", DASHBOARD_ROOT / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip("\"'")
            if key and val:
                os.environ.setdefault(key, val)

    global GEMINI_API_KEY, GEMINI_MODEL
    GEMINI_API_KEY = (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or GEMINI_API_KEY
    )
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", GEMINI_MODEL)


# Load .env as soon as config is imported (before other modules copy GEMINI_API_KEY).
load_env()
