#!/usr/bin/env python3
"""One-command launcher for the bot.

    python3 runner.py

Checks .env exists, installs missing dependencies, checks ffmpeg, then runs
bot.py and auto-restarts it if it ever crashes.
"""
import importlib.util
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED = {"aiogram": "aiogram", "yt_dlp": "yt-dlp",
            "instaloader": "instaloader", "dotenv": "python-dotenv",
            "spotdl": "spotdl"}


def check_env():
    if not (ROOT / ".env").exists():
        sys.exit("❌ No .env file found.\n"
                 "   Run:  cp .env.example .env  and put your BOT_TOKEN in it.")
    token = ""
    for line in (ROOT / ".env").read_text().splitlines():
        if line.strip().startswith("BOT_TOKEN="):
            token = line.split("=", 1)[1].strip()
    if not token or "your_token_here" in token:
        sys.exit("❌ BOT_TOKEN in .env is empty. Paste the token from @BotFather.")


def check_deps():
    missing = [pkg for mod, pkg in REQUIRED.items()
               if importlib.util.find_spec(mod) is None]
    if missing:
        print(f"📦 Installing missing packages: {', '.join(missing)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "-r", str(ROOT / "requirements.txt")])


def check_ffmpeg():
    if shutil.which("ffmpeg") is None:
        print("⚠️  ffmpeg not found — MP3 conversion and video merging will "
              "fail.\n   Install it:  sudo apt install ffmpeg")


def auto_update():
    """Keep yt-dlp fresh (YouTube breaks old versions) — at most once a day."""
    stamp = ROOT / ".last_update"
    if stamp.exists() and time.time() - stamp.stat().st_mtime < 86400:
        return
    print("🔄 Checking for yt-dlp updates...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U",
                    "yt-dlp"], check=False)
    stamp.touch()


def main():
    check_env()
    check_deps()
    check_ffmpeg()
    auto_update()
    print("🚀 Starting bot... (Ctrl+C to stop)")
    while True:
        try:
            proc = subprocess.run([sys.executable, str(ROOT / "bot.py")],
                                  cwd=ROOT)
        except KeyboardInterrupt:
            print("\n👋 Stopped.")
            return
        if proc.returncode == 0:
            return
        print(f"💥 Bot exited with code {proc.returncode} — "
              "restarting in 5 seconds (Ctrl+C to stop)...")
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n👋 Stopped.")
            return


if __name__ == "__main__":
    main()
