"""Make any downloaded video Telegram-playable.

Telegram inline playback wants: H.264 video (yuv420p), AAC audio, MP4
container with the moov atom at the front (+faststart). Instagram (and
occasionally others) serve files that violate one of these -> the client
shows a frozen first frame. normalize() fixes the file:
  - already H.264/AAC  -> fast remux (codec copy) with +faststart
  - anything else      -> transcode to H.264/AAC
probe() also returns width/height/duration so sends can include them,
plus supports_streaming — both improve playback on every client.
"""
import re
import subprocess
from pathlib import Path

from downloaders import ffmpeg_path

_VID_RE = re.compile(r"Video:\s*(\w+).*?(\d{2,5})x(\d{2,5})", re.S)
_AUD_RE = re.compile(r"Audio:\s*(\w+)")
_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def probe(path: Path) -> dict:
    """Codec/dimension/duration info via `ffmpeg -i` (no ffprobe needed,
    so it works with the bundled imageio-ffmpeg binary too)."""
    out = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    v = _VID_RE.search(out)
    a = _AUD_RE.search(out)
    d = _DUR_RE.search(out)
    dur = 0
    if d:
        h, m, s = d.groups()
        dur = int(float(s) + int(m) * 60 + int(h) * 3600)
    return {
        "vcodec": (v.group(1).lower() if v else ""),
        "width": int(v.group(2)) if v else 0,
        "height": int(v.group(3)) if v else 0,
        "acodec": (a.group(1).lower() if a else ""),
        "duration": dur,
    }


def normalize(path: Path) -> Path:
    """Return a Telegram-playable version of the video (replaces the file).
    Cheap remux when codecs are already right; transcode otherwise.
    On any failure the original file is returned untouched."""
    try:
        info = probe(path)
        out = path.with_name(path.stem + "_tg.mp4")
        if info["vcodec"] == "h264" and info["acodec"] in ("aac", ""):
            args = [ffmpeg_path(), "-y", "-i", str(path), "-c", "copy",
                    "-movflags", "+faststart", str(out)]
        else:
            args = [ffmpeg_path(), "-y", "-i", str(path),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart", str(out)]
        r = subprocess.run(args, capture_output=True)
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            path.unlink(missing_ok=True)
            return out
        out.unlink(missing_ok=True)
    except Exception:
        pass
    return path
