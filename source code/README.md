# Media Downloader Bot 🎬🎵📷

Telegram bot that downloads from **YouTube** (MP4 in multiple qualities or MP3),
**Instagram** (reels, videos, photos, carousels, profile lookup + stories), and
**Spotify** (tracks as MP3). Buttons-only UX, Persian/English, animated status
message with a live progress bar, and the status message is deleted after the
media is delivered. Works in groups when the bot is mentioned.

## Setup (Ubuntu / DigitalOcean droplet)

```bash
sudo apt update && sudo apt install -y python3-venv
git clone <your-repo> media-bot && cd media-bot
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env     # paste your BOT_TOKEN
./venv/bin/python runner.py            # one command runs everything
```

The token lives only in `.env` (git-ignored). Never put it in source.

### Instagram stories (optional)
Stories require a logged-in Instagram account. Either set `IG_USERNAME` +
`IG_PASSWORD` in `.env`, or (safer, avoids challenge loops) create a session
once and point `IG_SESSION_FILE` at it:

```bash
./venv/bin/instaloader --login YOUR_IG_USERNAME
# session saved under ~/.config/instaloader/session-YOUR_IG_USERNAME
```

Use a throwaway account — Instagram rate-limits and sometimes blocks
datacenter IPs. Public reels/posts work without login (yt-dlp fallback).

### Run as a service

```ini
# /etc/systemd/system/media-bot.service
[Unit]
Description=Telegram media downloader bot
After=network-online.target

[Service]
WorkingDirectory=/root/media-bot
ExecStart=/root/media-bot/venv/bin/python runner.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now media-bot
journalctl -u media-bot -f
```

## Bot flow

1. First contact → language buttons (🇮🇷 فارسی / 🇬🇧 English). `/lang` to change.
2. Main menu buttons: **🔎 YouTube search** and **📷 Instagram story**.
   - YouTube search: send a song or artist name → 20 numbered results with
     durations as tappable buttons → picking one opens the quality buttons.
3. Send any supported link:
   - **YouTube** → probes the video, shows quality buttons (1080p/720p/480p/360p…)
     plus MP3, downloads with a live `████░░ 63%` bar, uploads, deletes status.
   - **Instagram** post/reel → sends the video(s)/photo(s) with caption.
   - **Spotify** track → matches on YouTube via spotdl, sends the MP3.
4. `/story` → asks for a username → shows profile info (followers, posts, bio)
   and sends all active stories (needs IG login).
5. In a **group**: add the bot, mention `@yourbot` → it asks for a link and
   runs the whole flow in that group. `@yourbot <link>` in one message also works.

## Real-world smoke test checklist

Run these on the droplet after starting the bot:

- [ ] `/start` → language buttons appear; picking one confirms + shows menu
- [ ] 🔎 YouTube search button → send an artist name → 20 numbered results
- [ ] Tap a number → quality buttons appear → download completes normally
- [ ] YouTube link → quality buttons show only qualities the video actually has
- [ ] Pick 720p → progress bar updates, file arrives, status message disappears
- [ ] Pick MP3 → audio arrives with title
- [ ] Video over 49 MB → clean "too big" message (bots can't upload >50 MB)
- [ ] Instagram reel link → video arrives with caption
- [ ] Instagram photo/carousel post → all photos arrive
- [ ] `/story` + a public username → profile card + stories (with IG login set)
- [ ] Spotify track link → MP3 arrives
- [ ] In a group: `@bot` → asks for link; sending a link completes the flow there
- [ ] Wrong link ("hello") → polite unsupported-link reply

## Notes & limits

- Telegram bots can upload max ~50 MB; `MAX_FILE_MB=49` guards this. For bigger
  files you'd need a local Bot API server or MTProto (Telethon/Pyrogram).
- Message edits are throttled to ~1.6 s to stay under Telegram flood limits.
- ffmpeg is now bundled automatically (imageio-ffmpeg) — no manual install
  needed on Windows or Linux. A system ffmpeg is used if present.
- yt-dlp needs occasional updates when YouTube changes:
  `./venv/bin/pip install -U yt-dlp spotdl`

## runner.py

`python3 runner.py` is the single entry point: it verifies `.env` and the
token, installs any missing packages, warns if ffmpeg is absent, then starts
the bot and auto-restarts it 5 s after any crash. Ctrl+C stops cleanly.

## What's new in the full edition

- **SQLite persistence** (`botdata.db`, WAL mode): languages, live buttons,
  pending flows, file cache, stats — all survive restarts and crashes.
- **❌ Cancel button** on every download; stops yt-dlp/spotdl mid-transfer.
- **Admin panel** (set `ADMIN_ID` in .env): `/stats`, `/health`, `/ban <id>`,
  `/unban <id>`, `/broadcast <text>`.
- **Per-user daily quota** (`DAILY_LIMIT`, default 40) + ban list.
- **Hard download timeout** (`DOWNLOAD_TIMEOUT`, default 600s) — nothing hangs.
- **Invalid token = clean exit**: runner/systemd no longer restart-loop on it.
- **Test suite ships with the project**: `python tests/test_all.py`.
- **Deployment**: `deploy/media-bot.service` (systemd) and
  `deploy/docker-compose.yml` (Docker, with optional local Bot API).

## Breaking the 50MB limit (optional)

Run Telegram's own Bot API server next to the bot; uploads then go to 2GB:
1. Get `api_id`/`api_hash` at https://my.telegram.org
2. Uncomment the `telegram-api` service in `deploy/docker-compose.yml`
3. Set `LOCAL_API_URL=http://telegram-api:8081` in `.env`
The bot auto-raises `MAX_FILE_MB` to 1900 when `LOCAL_API_URL` is set.

## Deploying on the droplet (recommended over your PC)

```bash
sudo mkdir -p /opt/media-bot && sudo chown $USER /opt/media-bot
# copy the project there, then:
cd /opt/media-bot && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env
sudo cp deploy/media-bot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now media-bot
journalctl -u media-bot -f
```

## Known limits (honest section)

- Instagram requires cookies/login by Instagram's design; sessions expire and
  need re-export. `/health` shows current IG auth status.
- Inline mode requires BotFather → Inline Feedback = 100%, or placeholders
  never resolve. Inline carousels send the first item in-chat, the rest to PM.
- The last seconds of the progress bar are a smoothing animation by design.
- spotdl percentage parsing depends on its CLI output format; if an update
  changes it, downloads still work but show the indeterminate animation.
