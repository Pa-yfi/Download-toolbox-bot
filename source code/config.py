"""Configuration — everything secret comes from .env, nothing hardcoded."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is missing. Copy .env.example to .env and set it.")

IG_USERNAME = os.getenv("IG_USERNAME", "").strip()
IG_PASSWORD = os.getenv("IG_PASSWORD", "").strip()
IG_SESSION_FILE = os.getenv("IG_SESSION_FILE", "").strip()
IG_COOKIES_FILE = os.getenv("IG_COOKIES_FILE", "").strip()
COOKIES_FROM_BROWSER = os.getenv("COOKIES_FROM_BROWSER", "").strip()
INLINE_DUMP_CHAT = os.getenv("INLINE_DUMP_CHAT", "").strip()

# Self-hosted Bot API server (https://github.com/tdlib/telegram-bot-api)
# lifts the 50MB upload limit to 2GB. e.g. http://127.0.0.1:8081
LOCAL_API_URL = os.getenv("LOCAL_API_URL", "").strip()

_default_cap = 1900 if LOCAL_API_URL else 49
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", str(_default_cap)))

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "40"))          # downloads/user/day
_default_to = 3600 if LOCAL_API_URL else 600
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", str(_default_to)))

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads")).resolve()
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = Path("./botdata.db").resolve()
