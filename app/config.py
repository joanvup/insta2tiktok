from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
DATA_DIR = BASE_DIR / "data"

VIDEOS_DIR = DATA_DIR / "videos"
THUMBS_DIR = DATA_DIR / "thumbs"
SESSIONS_DIR = DATA_DIR / "sessions"
LOGS_DIR = DATA_DIR / "logs"
SCREENSHOTS_DIR = LOGS_DIR / "screenshots"
BROWSER_DIR = DATA_DIR / "browser"
INSTAGRAM_PROFILE_DIR = BROWSER_DIR / "instagram"
TIKTOK_PROFILE_DIR = BROWSER_DIR / "tiktok"
DB_PATH = DATA_DIR / "app.db"
INSTAGRAM_COOKIES_PATH = SESSIONS_DIR / "instagram_cookies.json"

APP_TITLE = "insta2tiktok"
APP_HOST = "127.0.0.1"
APP_PORT = 8000

DEFAULT_REELS = 12
MAX_REELS = 30
MAX_CAPTION = 2200
MAX_ATTEMPTS = 5
FETCH_COOLDOWN_SECONDS = 60
POST_DELAY_SECONDS = 30
CONNECT_TIMEOUT_SECONDS = 300

TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?lang=en"
INSTAGRAM_LOGIN_URL = "https://www.instagram.com/accounts/login/"

MAX_VIDEO_BYTES = 4 * 1024 * 1024 * 1024

UPLOAD_READY_TIMEOUT_MS = 120_000
POST_READY_TIMEOUT_MS = 600_000
POST_CONFIRM_TIMEOUT_MS = 180_000


def ensure_dirs() -> None:
    for path in (
        DATA_DIR,
        VIDEOS_DIR,
        THUMBS_DIR,
        SESSIONS_DIR,
        LOGS_DIR,
        SCREENSHOTS_DIR,
        BROWSER_DIR,
        INSTAGRAM_PROFILE_DIR,
        TIKTOK_PROFILE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
