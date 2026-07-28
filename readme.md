# 📚 LEARNING.md — How This Bot Works, File by File

This is the teaching companion to the project. `README.md` tells you how to
*run* the bot; this file teaches you how it *works* — every file, every
important pattern, and why each design decision was made. Read it top to
bottom once, then use it as a reference while reading the code.

---

## 1. The Big Picture

A Telegram bot is just a program that talks to Telegram's HTTP API:

```
   Telegram servers                         Your machine
 ┌──────────────────┐   "any updates?"    ┌──────────────────┐
 │  users' messages  │ ◄────────────────── │     bot.py        │
 │  button taps      │ ──────────────────► │  (long polling)   │
 │  inline queries   │   updates JSON      └──────────────────┘
 └──────────────────┘
```

The bot **long-polls**: it asks Telegram "anything new?" in a loop, and
Telegram holds the connection open until something happens. Each thing that
happens (a message, a button tap, an inline query) arrives as an **update**.
The `aiogram` library turns updates into Python objects and routes them to
your **handler functions** based on filters.

Everything is **async** (`async def` / `await`): one single thread runs an
*event loop* that juggles hundreds of conversations at once. The rule:
never block that loop. Slow, blocking work (downloading a video, running
ffmpeg) is pushed onto worker threads with `asyncio.to_thread(...)` so the
loop stays free to answer other users.

The flow of one YouTube request, end to end:

```
user sends link
  └─► on_text() detects platform ─► flow_youtube()
        └─► yt.probe() in a thread ─► quality buttons appear
user taps "720p"
  └─► cb_youtube() ─► UserJob lock ─► yt.download() in a thread
        │                └─ progress hook writes numbers ──┐
        │                                                  ▼
        │            StatusMessage renderer task edits "███░░ 63%"
        └─► send_media() ─► video normalized ─► uploaded ─► status deleted
```

---

## 2. Project Map

```
media-bot/
├── runner.py            ← start here: one-command launcher
├── bot.py               ← the brain: all handlers and flows
├── config.py            ← reads .env, exposes settings
├── db.py                ← SQLite persistence (state survives restarts)
├── i18n.py              ← every user-facing string, fa + en
├── progress.py          ← the animated status message engine
├── downloaders/
│   ├── __init__.py      ← URL detection, ffmpeg finder, Cancelled
│   ├── youtube.py       ← yt-dlp: probe, search, download
│   ├── instagram.py     ← posts/reels/stories, cookie auth
│   ├── spotify.py       ← spotdl + YouTube-match fallback
│   └── media.py         ← makes videos Telegram-playable
├── tests/test_all.py    ← 20 offline tests, no token needed
├── deploy/              ← systemd unit, Dockerfile, compose
├── .env.example         ← template for secrets (copy to .env)
└── requirements.txt     ← Python dependencies
```

---

## 3. File-by-File Walkthrough

### 3.1 `runner.py` — the launcher

The only file you run directly. It does four jobs before starting the bot:

1. **`check_env()`** — refuses to start without a real `BOT_TOKEN` in
   `.env`, with a clear message telling you what to do.
2. **`check_deps()`** — imports each required library; if any is missing it
   runs `pip install -r requirements.txt` for you.
3. **`check_ffmpeg()` / `auto_update()`** — warns about ffmpeg and
   refreshes `yt-dlp` at most once a day (YouTube changes break old
   versions).
4. **The restart loop** — runs `bot.py` as a subprocess. If it crashes
   (exit code ≠ 0), wait 5 seconds and restart. If it exits *cleanly*
   (code 0 — e.g. an invalid token), stop. That exit-code contract is why
   a bad token doesn't hammer Telegram forever.

**Lesson:** separating the launcher from the app lets the app crash freely;
the launcher is tiny and never crashes.

### 3.2 `.env`, `.env.example`, `config.py` — configuration

Secrets never live in code (the "12-factor app" rule). `.env` holds your
real values and is git-ignored; `.env.example` is the committed template
showing which variables exist.

`config.py` loads `.env` with `python-dotenv`, converts strings to the
right types, applies smart defaults (e.g. if `LOCAL_API_URL` is set, the
file-size cap jumps from 49MB to 1900MB and the timeout from 600s to
3600s), and crashes early with a helpful message if the token is missing.
Every other file imports settings from here — one source of truth.

### 3.3 `i18n.py` — internationalization

One dictionary: `STRINGS["key"]["fa"|"en"]`. The helper
`t(lang, key, **fmt)` fetches and `.format()`s a string. Handlers never
contain literal user-facing text — they say `t(lang, "downloading")`.
Adding a third language means adding one key per string, touching zero
logic. A test (`test_i18n_complete`) scans the code for every `t()` call
and fails if any key is missing a translation.

### 3.4 `db.py` — persistence with SQLite

Early versions used a JSON file: simple, but a crash mid-write corrupts it
and every restart wiped in-memory state (all live buttons died). SQLite in
**WAL mode** fixes both: writes are atomic, and readers don't block writers.

Tables:
- `users` — language, ban flag, today's download count
- `pending` — "this user in this chat is mid-flow" (e.g. typed the search
  button, we're waiting for the song name). Because it's in the DB, a
  restart doesn't forget the conversation.
- `cache` — short-lived button data (URL behind a quality button, search
  results) with a TTL, so buttons keep working across restarts.
- `files` — Telegram `file_id`s of everything ever delivered → instant
  re-sends with zero downloading.
- `stats`, `meta` — counters and the bot's own identity.

Two patterns worth learning:

**Check vs. consume.** `quota_ok()` only *reads* whether the user has
downloads left; `quota_use()` *charges* one. The charge happens only after
a successful delivery — failed or cancelled downloads are free. Splitting
read from write is how you avoid punishing users for your errors.

**Identity-scoped caches.** `file_id`s are only valid for the token that
uploaded them. `ensure_bot_identity()` stores the bot's ID; if it ever
changes (token regenerated), the whole file cache is purged so users never
receive dead references.

### 3.5 `progress.py` — the animation engine

The hardest bug in this project lived here, and the fix is a pattern worth
memorizing: **single-writer rendering**.

Problem: download progress arrives from a *worker thread* (yt-dlp's hook),
while the spinner animation runs in the *event loop*. Two writers editing
the same Telegram message = race conditions = the frozen "0%" bar you saw.

Solution: nobody edits the message except **one renderer task**. Everyone
else — threads included — just assigns plain variables:

```python
st.set_pct("downloading", 63.0, "21/33 MB")   # thread-safe: it's just
                                              # attribute assignment (GIL)
```

Every 1.5s the renderer reads the current state, composes the text, and
edits the message — throttled, so Telegram's flood limits are respected.

The **smooth animation** adds a second idea: the bar keeps a *displayed*
percentage separate from the *real* one. Each frame it climbs a random
7–16 points toward the real value (never past it, never backwards). When
the download finishes, `complete()` forces fast frames until the bar
visibly fills — so even a 2-second download shows a satisfying
12 → 27 → 44 → 61 → 80 → 100 instead of teleporting to full.

`StatusMessage` also handles: an optional ❌ Cancel keyboard, inline
messages (edited by `inline_message_id` instead of chat+message id),
`finish(text)` for errors, and `delete()` after successful delivery.

### 3.6 `downloaders/__init__.py` — shared bits

- **`detect_platform(text)`** — regexes that classify a URL as
  youtube/instagram/spotify. Centralized so chat, mention, and inline
  flows all agree.
- **`ffmpeg_path()`** — system ffmpeg if installed, otherwise the binary
  bundled by the `imageio-ffmpeg` pip package. This removed an entire
  class of "nothing gets sent" failures (no ffmpeg → every MP3 and merged
  video died before a file existed).
- **`Cancelled`** — the exception that means "user pressed ❌". Defined
  here so every downloader and every flow can speak it.

### 3.7 `downloaders/youtube.py`

Built on **yt-dlp**. Three functions:

- **`probe(url)`** — fetches metadata *without* downloading: title,
  which resolutions actually exist, and a size estimate per resolution
  (so buttons can say `720p · 31MB` and warn `⚠️` when over the cap).
- **`search(query, 20)`** — `ytsearch20:` with `extract_flat=True`, which
  returns titles/durations fast without probing each video.
- **`download(url, kind, height, on_progress, cancel)`** — the format
  selector string is the interesting part:
  `bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]...`
  means "best ≤720p video merged with best audio; fall back progressively."
  The merge is why ffmpeg is required. The **progress hook** is a callback
  yt-dlp fires constantly; ours computes a percentage (handling merged
  downloads where byte counts restart per file, and fragmented streams
  where the total is unknown), and checks the cancel event — raising
  `Cancelled` from inside the hook is how we abort yt-dlp mid-transfer.

### 3.8 `downloaders/instagram.py`

Instagram actively fights scraping, so this file is mostly *strategy*:

1. **yt-dlp first** for reels/videos — most reliable, and it can borrow
   your browser's logged-in cookies (`COOKIES_FROM_BROWSER=firefox` or a
   `cookies.txt`).
2. **instaloader fallback** for photo posts and carousels, configured with
   `max_connection_attempts=1` — its default behavior on a 429 is to
   silently retry for 10+ minutes, which once froze the whole bot.
3. **Stories** require a real login session by Instagram's design.

Errors that smell like auth (429, 401, 403, login, checkpoint) are wrapped
in a custom `IGAuthNeeded` exception, which the bot turns into a bilingual
help message explaining exactly which `.env` line to set. **Lesson:**
convert vendor-specific failure text into your own exception types at the
boundary; the rest of the code then handles *meanings*, not strings.

### 3.9 `downloaders/spotify.py`

Bots can't download from Spotify itself. Two-stage pipeline:

1. **spotdl** — matches the track on YouTube Music and downloads it.
   Its output is parsed for `NN%` to feed the progress bar, and its last
   log lines are kept so failures are diagnosable.
2. **Fallback** — if spotdl fails (it depends on Spotify internals and
   breaks often): fetch the public track page, read the
   `og:title` / `og:description` meta tags ("Alan Walker · Song · 2015"),
   build the query "Alan Walker Faded official audio", search YouTube,
   and download the top match as MP3 through the YouTube module.

Note the ordering subtlety in `download()`: `Cancelled` is re-raised
*before* the generic `except` — a user pressing ❌ must not accidentally
trigger the fallback.

### 3.10 `downloaders/media.py`

Why did Instagram videos arrive frozen? Telegram's inline player wants:
**H.264** video, **AAC** audio, **yuv420p** pixels, MP4 container with the
`moov` atom (the index) at the **front** (`+faststart`). Instagram often
violates one of these; Telegram then shows only the poster frame.

- `probe(path)` — runs `ffmpeg -i` and regex-parses its stderr for codecs,
  dimensions, duration. (Deliberately not ffprobe — the bundled
  imageio-ffmpeg ships only ffmpeg.)
- `normalize(path)` — if codecs are already right: a *remux* (`-c copy
  -movflags +faststart`), sub-second and lossless. Otherwise: transcode to
  H.264/AAC. On any failure it returns the original file — degrade, never
  break.

`send_media` routes every outgoing video through this, and attaches
width/height/duration plus `supports_streaming=True` — metadata that alone
fixes many "won't play" cases.

### 3.11 `bot.py` — the brain

Read it in this order:

**Keyboards** (top): small functions returning `InlineKeyboardMarkup`.
Buttons carry `callback_data` strings like `yt:mp4:720:a1b2c3` — a tiny
routing protocol (≤64 bytes!). The long data (the URL) lives in the DB
cache; the button carries only the key.

**`gate()`** — ban check + quota *check* (never consume; see §3.4).

**`send_media()`** — the single choke point every delivery goes through:
size guard, video normalization, streaming metadata, 900s upload timeout,
keyword-only API args (a positional-argument bug once broke everything
when aiogram inserted a new first parameter — keywords are
reorder-proof).

**`UserJob`** — an async context manager combining three things:
per-user "one job at a time" (`BUSY` set), a global 3-slot semaphore
(`DL_SEM`) so ten users can't melt the machine, and a fresh
`threading.Event` registered in `JOBS` so the ❌ button can reach into a
running download. `__aexit__` releases everything **even on exceptions** —
that guarantee is what makes locks safe.

**The flows** (`flow_youtube`, `cb_youtube`, `flow_yt_search`, `cb_pick`,
`flow_instagram`, `flow_story`, `flow_spotify`) all share one skeleton:

```
gate → status message → UserJob → begin animation
→ download (thread, cancellable, timeout) → complete animation
→ upload → cache file_id → charge quota → delete status
   └─ except Cancelled / IGAuthNeeded / Timeout / anything → friendly finish()
```

**Inline mode** — the trickiest subsystem, with two hard-won Telegram
facts baked in:

1. Inline queries must answer *instantly*, so no downloading in
   `on_inline()` — it only builds result lists (plus ⚡ cached entries).
2. The bot can only edit the sent placeholder if Telegram gives it an
   `inline_message_id` — and Telegram **only does that when the result
   carries an inline keyboard**. That's why every download result has a ⏳
   button. Also requires BotFather → Inline Feedback = 100%.

The lifecycle: user taps → `chosen_inline_result` arrives →
`_inline_job()` downloads with the same animated status (edited via
`inline_message_id`) → uploads the file through the user's PM or a dump
channel just to obtain a `file_id` → `edit_message_media()` **morphs the
placeholder into the actual video** in the other person's chat. Carousel
leftovers go to the user's PM.

**`on_text()`** — the router: group mentions, pending states from the DB
(search term expected? username expected?), then platform detection →
dispatch. In groups it stays silent unless mentioned or mid-flow.

**Admin commands** — `/stats`, `/health` (ffmpeg, yt-dlp version, IG auth,
API mode), `/ban`, `/unban`, `/broadcast` (runs as a background task so a
big user list doesn't freeze the handler). Guarded by `ADMIN_ID`.

**`main()`** — sweep orphaned temp files, build the Bot (pointing at a
local API server if configured), verify the token (invalid → clean exit 0,
see runner contract), purge the file cache if the token changed, start the
daily yt-dlp refresher, poll.

### 3.12 `tests/test_all.py`

20 tests, zero network, zero real token. The key technique is
**mock objects**: `MockBot` implements the same methods as aiogram's `Bot`
but just records calls into a list; assertions then inspect the list.
Mock methods are deliberately **keyword-only** (`*,`) so any positional
API call — the class of bug that once broke everything — fails the suite
instantly. Several tests exist purely as **regression guards**: they
encode a past bug (the `dir()` hack, missing inline keyboards, quota
burned on failure) so it can never silently return. Run with:

```
python tests/test_all.py        # or: python -m pytest tests/ -v
```

### 3.13 `deploy/`

- `media-bot.service` — systemd unit for the droplet.
  `RestartPreventExitStatus=0` mirrors the runner's exit-code contract.
- `Dockerfile` + `docker-compose.yml` — containerized deployment, with a
  commented-out `telegram-bot-api` service that lifts uploads to 2GB.

---

## 4. Concepts Glossary

- **Event loop** — the single async scheduler; never block it.
- **`asyncio.to_thread`** — run blocking code on a worker thread and
  `await` the result.
- **`threading.Event`** — a thread-safe flag; `.set()` from anywhere,
  `.is_set()` checked inside download loops = cooperative cancellation.
- **Semaphore** — a counter of permitted concurrent holders; our download
  slots.
- **`callback_data`** — the ≤64-byte string a button sends back; treat it
  as a routing key, keep payloads in the DB.
- **`file_id`** — Telegram's handle for an uploaded file; re-sending by id
  is instant and free, but ids are bound to your bot token.
- **WAL** — SQLite's write-ahead log; crash-safe, concurrent-friendly.
- **faststart / moov atom** — MP4's index; must be at the file's front for
  streaming playback.
- **Flood limits** — Telegram throttles rapid edits; hence the 1.5s render
  tick and `TelegramRetryAfter` handling.

## 5. How to Extend: Adding a New Platform (e.g. TikTok)

1. Add a regex + branch in `detect_platform()`.
2. Create `downloaders/tiktok.py` with
   `async def download(url, on_progress, cancel=None)` (yt-dlp supports
   TikTok, so it's mostly a copy of the YouTube module minus quality
   selection).
3. Add `flow_tiktok()` in `bot.py` following the shared skeleton, and a
   branch in `dispatch()` and in the inline builders.
4. Add strings to `i18n.py` (both languages).
5. Add a test with a mocked downloader.

That checklist *is* the architecture lesson: detection, download,
flow, strings, test — five layers, each in its own file.
