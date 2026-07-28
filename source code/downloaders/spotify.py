"""Spotify with a two-stage pipeline.

Stage 1: spotdl (matches on YouTube Music, best metadata).
Stage 2: if spotdl fails for ANY reason — a common occurrence since it
depends on Spotify internals — read the track title/artist straight from
the public Spotify page (og: meta tags / oEmbed, no API key needed),
search YouTube for it, and download the top match as MP3 via yt-dlp.
"""
import asyncio
import logging
import re
import subprocess
import sys
import uuid
from pathlib import Path

import requests

from config import DOWNLOAD_DIR
from downloaders import Cancelled, ffmpeg_path

log = logging.getLogger("spotify")
PCT_RE = re.compile(r"(\d{1,3})%")
UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/126.0 Safari/537.36")}
OG_TITLE = re.compile(r'property="og:title"\s+content="([^"]+)"')
OG_DESC = re.compile(r'property="og:description"\s+content="([^"]+)"')


def _spotdl(url: str, on_progress, cancel=None) -> tuple[Path, str]:
    dest = DOWNLOAD_DIR / f"sp_{uuid.uuid4().hex[:8]}"
    dest.mkdir()
    cmd = [sys.executable, "-m", "spotdl", "download", url,
           "--output", str(dest / "{artists} - {title}.{output-ext}"),
           "--format", "mp3", "--ffmpeg", ffmpeg_path()]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    last, tail = 0, []
    for line in proc.stdout:  # type: ignore[union-attr]
        if cancel is not None and cancel.is_set():
            proc.kill()
            import shutil
            shutil.rmtree(dest, ignore_errors=True)
            raise Cancelled()
        tail.append(line.strip())
        tail = tail[-8:]
        m = PCT_RE.search(line)
        if m:
            pct = min(100, int(m.group(1)))
            if pct > last:
                last = pct
                on_progress(pct, "")
    proc.wait()
    files = sorted(dest.glob("*.mp3"), key=lambda p: p.stat().st_size,
                   reverse=True)
    if not files:
        raise RuntimeError("spotdl: " + (" | ".join(tail[-3:]) or "no output"))
    return files[0], files[0].stem


def parse_track_page(html: str) -> tuple[str, str]:
    """Extract (search_query, display_title) from a Spotify track page."""
    title = artist = ""
    m = OG_TITLE.search(html)
    if m:
        title = m.group(1).strip()
    m = OG_DESC.search(html)
    if m:
        # og:description looks like: "Alan Walker · Song · 2015"
        artist = m.group(1).split("·")[0].strip()
    if not title:
        raise RuntimeError("could not read track info from Spotify page")
    query = f"{artist} {title}".strip()
    display = f"{artist} - {title}" if artist else title
    return query, display


def _track_query(url: str) -> tuple[str, str]:
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    try:
        return parse_track_page(r.text)
    except RuntimeError:
        # last resort: oEmbed gives at least the track name
        j = requests.get("https://open.spotify.com/oembed",
                         params={"url": url}, headers=UA, timeout=20).json()
        title = (j.get("title") or "").strip()
        if not title:
            raise
        return title, title


async def download(url: str, on_progress, cancel=None) -> tuple[Path, str]:
    """Returns (path, title). Tries spotdl, falls back to YouTube match."""
    try:
        return await asyncio.to_thread(_spotdl, url, on_progress, cancel)
    except Cancelled:
        raise
    except Exception as e:
        log.warning("spotdl failed (%s) — falling back to YouTube match",
                    str(e)[:200])
    from downloaders import youtube as yt
    query, display = await asyncio.to_thread(_track_query, url)
    results = await yt.search(f"{query} official audio", 1)
    if not results:
        raise RuntimeError(f"no YouTube match found for: {query}")
    path = await yt.download(results[0]["url"], "mp3", 0, on_progress, cancel)
    return path, display
