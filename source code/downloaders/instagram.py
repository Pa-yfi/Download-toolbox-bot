"""Instagram: posts, reels, photos, profiles, stories.

Strategy (Instagram aggressively blocks anonymous scraping with 429s):
  1. Videos/reels: yt-dlp FIRST, with your browser cookies if configured —
     by far the most reliable path.
  2. Photo posts / carousels: instaloader, ideally with a login session.
  3. Profiles & stories: instaloader; stories require login by design.

Instaloader is configured to FAIL FAST (1 attempt, short timeout) instead of
its default behaviour of silently retrying for 10+ minutes on a 429.
"""
import asyncio
import re
import uuid
from pathlib import Path

import instaloader
from downloaders import Cancelled
from config import (DOWNLOAD_DIR, IG_USERNAME, IG_PASSWORD, IG_SESSION_FILE,
                    IG_COOKIES_FILE, COOKIES_FROM_BROWSER)

SHORTCODE_RE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)")

_L: instaloader.Instaloader | None = None
_logged_in = False


class IGAuthNeeded(Exception):
    """Instagram refused anonymous access (429 / login wall)."""


def _loader() -> instaloader.Instaloader:
    global _L, _logged_in
    if _L is None:
        _L = instaloader.Instaloader(
            quiet=True, download_comments=False, save_metadata=False,
            download_geotags=False, post_metadata_txt_pattern="",
            max_connection_attempts=1,      # fail fast, no 666-second retries
            request_timeout=15.0,
        )
        try:
            if IG_SESSION_FILE:
                _L.load_session_from_file(IG_USERNAME or None, IG_SESSION_FILE)
                _logged_in = True
            elif IG_USERNAME and IG_PASSWORD:
                _L.login(IG_USERNAME, IG_PASSWORD)
                _logged_in = True
        except Exception:
            _logged_in = False
    return _L


def stories_enabled() -> bool:
    _loader()
    return _logged_in


def _is_auth_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(k in msg for k in ("429", "too many requests", "login",
                                  "401", "403", "checkpoint", "rate"))


def _ytdlp_opts(dest: Path, cancel=None) -> dict:
    from downloaders import ffmpeg_path
    opts = {
        "quiet": True, "no_warnings": True,
        "outtmpl": str(dest / "%(id)s.%(ext)s"),
        "ffmpeg_location": ffmpeg_path(),
        "restrictfilenames": True,
    }
    if IG_COOKIES_FILE:
        opts["cookiefile"] = IG_COOKIES_FILE
    elif COOKIES_FROM_BROWSER:
        opts["cookiesfrombrowser"] = (COOKIES_FROM_BROWSER,)
    if cancel is not None:
        def _hook(_d):
            if cancel.is_set():
                raise Cancelled()
        opts["progress_hooks"] = [_hook]
    return opts


def _dl_url(L, url: str, dest: Path, suffix: str) -> Path:
    path = dest / f"{uuid.uuid4().hex[:8]}{suffix}"
    L.context.write_raw(L.context.get_raw(url), path)
    return path


def _fetch_post_ytdlp(url: str, cancel=None) -> dict:
    """Primary path for reels/videos — most reliable, uses cookies if set."""
    import yt_dlp
    dest = DOWNLOAD_DIR / f"ig_{uuid.uuid4().hex[:8]}"
    dest.mkdir()
    try:
        with yt_dlp.YoutubeDL(_ytdlp_opts(dest, cancel)) as y:
            info = y.extract_info(url, download=True)
    except Exception:
        if cancel is not None and cancel.is_set():
            import shutil
            shutil.rmtree(dest, ignore_errors=True)
            raise Cancelled()
        raise
    files = [p for p in dest.glob("*") if p.suffix.lower()
             in (".mp4", ".webm", ".jpg", ".jpeg", ".png", ".webp")]
    files.sort(key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        raise RuntimeError("no media found")
    items = [(f, "video" if f.suffix.lower() in (".mp4", ".webm") else "photo")
             for f in files]
    return {"caption": (info.get("description") or "")[:400], "items": items}


def _fetch_post_instaloader(url: str, cancel=None) -> dict:
    """Fallback — needed for photo posts and carousels."""
    m = SHORTCODE_RE.search(url)
    if not m:
        raise RuntimeError("not a post/reel link")
    L = _loader()
    try:
        post = instaloader.Post.from_shortcode(L.context, m.group(1))
    except Exception as e:
        if _is_auth_error(e):
            raise IGAuthNeeded(str(e)) from e
        raise
    dest = DOWNLOAD_DIR / f"ig_{uuid.uuid4().hex[:8]}"
    dest.mkdir()
    items: list[tuple[Path, str]] = []
    if post.typename == "GraphSidecar":
        for node in post.get_sidecar_nodes():
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if node.is_video:
                items.append((_dl_url(L, node.video_url, dest, ".mp4"), "video"))
            else:
                items.append((_dl_url(L, node.display_url, dest, ".jpg"), "photo"))
    elif post.is_video:
        items.append((_dl_url(L, post.video_url, dest, ".mp4"), "video"))
    else:
        items.append((_dl_url(L, post.url, dest, ".jpg"), "photo"))
    return {"caption": (post.caption or "").strip()[:400], "items": items}


def _fetch_post(url: str, cancel=None) -> dict:
    try:
        return _fetch_post_ytdlp(url, cancel)
    except Cancelled:
        raise
    except Exception as first:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        try:
            return _fetch_post_instaloader(url, cancel)
        except (IGAuthNeeded, Cancelled):
            raise
        except Exception:
            if _is_auth_error(first):
                raise IGAuthNeeded(str(first)) from first
            raise first


async def fetch_post(url: str, cancel=None) -> dict:
    return await asyncio.wait_for(
        asyncio.to_thread(_fetch_post, url, cancel), timeout=180)


def _profile(username: str) -> dict:
    L = _loader()
    try:
        p = instaloader.Profile.from_username(L.context, username)
    except Exception as e:
        if _is_auth_error(e):
            raise IGAuthNeeded(str(e)) from e
        raise
    return {"username": p.username, "full_name": p.full_name or p.username,
            "followers": p.followers, "posts": p.mediacount,
            "bio": (p.biography or "")[:200], "userid": p.userid}


async def profile(username: str) -> dict:
    return await asyncio.wait_for(asyncio.to_thread(_profile, username),
                                  timeout=60)


def _stories(userid: int, cancel=None) -> list[tuple[Path, str]]:
    L = _loader()
    if not _logged_in:
        raise RuntimeError("stories require login")
    dest = DOWNLOAD_DIR / f"st_{uuid.uuid4().hex[:8]}"
    dest.mkdir()
    items: list[tuple[Path, str]] = []
    for story in L.get_stories(userids=[userid]):
        for item in story.get_items():
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if item.is_video:
                items.append((_dl_url(L, item.video_url, dest, ".mp4"), "video"))
            else:
                items.append((_dl_url(L, item.url, dest, ".jpg"), "photo"))
    return items


async def stories(userid: int, cancel=None) -> list[tuple[Path, str]]:
    return await asyncio.wait_for(
        asyncio.to_thread(_stories, userid, cancel), timeout=300)
