"""YouTube via yt-dlp: probe available qualities, download mp4/mp3 with progress."""
import asyncio
import uuid
from pathlib import Path
import yt_dlp
from config import DOWNLOAD_DIR
from downloaders import Cancelled, ffmpeg_path

MP4_LADDER = [1080, 720, 480, 360, 240]


def _probe(url: str) -> dict:
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as y:
        return y.extract_info(url, download=False)


async def probe(url: str) -> dict:
    """Return {'title', 'duration', 'heights': [available mp4 heights]}."""
    info = await asyncio.to_thread(_probe, url)
    fmts = info.get("formats", [])
    heights = sorted(
        {f.get("height") for f in fmts
         if f.get("height") and f.get("vcodec") not in (None, "none")},
        reverse=True,
    )
    avail = [h for h in MP4_LADDER if any(x >= h for x in heights)] or [360]
    # per-quality size estimates for button labels
    audio = [f for f in fmts if f.get("acodec") not in (None, "none")
             and f.get("vcodec") in (None, "none")]
    asz = 0
    if audio:
        ba = max(audio, key=lambda f: f.get("abr") or 0)
        asz = ba.get("filesize") or ba.get("filesize_approx") or 0
    sizes: dict[int, float | None] = {}
    for h in avail:
        cands = [f for f in fmts if f.get("height") == h
                 and f.get("vcodec") not in (None, "none")]
        best = max(cands, key=lambda f: f.get("tbr") or 0, default=None)
        if best:
            vsz = best.get("filesize") or best.get("filesize_approx") or 0
            extra_a = 0 if best.get("acodec") not in (None, "none") else asz
            sizes[h] = round((vsz + extra_a) / 1e6, 1) if vsz else None
        else:
            sizes[h] = None
    dur = info.get("duration") or 0
    mp3_size = round(dur * 192 / 8 / 1000, 1) if dur else None  # 192kbps
    return {"title": info.get("title", "video"), "duration": dur,
            "heights": avail, "sizes": sizes, "mp3_size": mp3_size}


def _download(url: str, kind: str, height: int, on_progress,
              cancel=None) -> Path:
    out = DOWNLOAD_DIR / f"yt_{uuid.uuid4().hex[:8]}"
    out.mkdir()

    state = {"prev_files": 0.0}

    def hook(d):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        st = d.get("status")
        if st == "finished":
            # a stream file completed (video part of a merge, etc.)
            state["prev_files"] += (d.get("total_bytes")
                                    or d.get("downloaded_bytes") or 0)
        elif st == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            cum = (state["prev_files"] + done) / 1e6
            if total:
                on_progress(done * 100 / total,
                            f"{done/1e6:.1f} / {total/1e6:.1f} MB")
            else:
                # unknown size (fragmented stream): keep the UI alive with MB
                on_progress(None, f"{cum:.1f} MB")

    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "outtmpl": str(out / "%(title).80s.%(ext)s"),
        "progress_hooks": [hook],
        "ffmpeg_location": ffmpeg_path(),
        "restrictfilenames": True,   # safe filenames on Windows
    }
    if kind == "mp3":
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    else:
        opts.update({
            "format": (f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]"
                       f"/best[height<={height}][ext=mp4]/best[height<={height}]/best"),
            "merge_output_format": "mp4",
        })

    try:
        with yt_dlp.YoutubeDL(opts) as y:
            y.extract_info(url, download=True)
    except Exception:
        if cancel is not None and cancel.is_set():
            import shutil
            shutil.rmtree(out, ignore_errors=True)
            raise Cancelled()
        raise

    files = sorted(out.glob("*"), key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        raise RuntimeError("download produced no file")
    return files[0]


async def download(url: str, kind: str, height: int, on_progress,
                   cancel=None) -> Path:
    """kind: 'mp4'|'mp3'. on_progress(pct, extra_text) is thread-safe."""
    return await asyncio.to_thread(_download, url, kind, height,
                                   on_progress, cancel)


def _search(query: str, n: int = 20) -> list[dict]:
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(f"ytsearch{n}:{query}", download=False)
    results = []
    for e in (info.get("entries") or []):
        if not e:
            continue
        vid = e.get("id", "")
        results.append({
            "url": e.get("url") or f"https://www.youtube.com/watch?v={vid}",
            "title": (e.get("title") or "?")[:80],
            "duration": e.get("duration") or 0,
        })
    return results


async def search(query: str, n: int = 20) -> list[dict]:
    """Return up to n results: [{'url','title','duration'}, ...]."""
    return await asyncio.to_thread(_search, query, n)


def fmt_duration(sec) -> str:
    sec = int(sec or 0)
    if not sec:
        return ""
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"
