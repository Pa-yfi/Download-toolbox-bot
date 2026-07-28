import re

YT_RE = re.compile(r"(https?://)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/\S+", re.I)
IG_RE = re.compile(r"(https?://)?(www\.)?instagram\.com/\S+", re.I)
SP_RE = re.compile(r"(https?://)?(open\.)?spotify\.com/\S+|spotify:(track|album|playlist):\S+", re.I)
URL_RE = re.compile(r"https?://\S+", re.I)


def detect_platform(text: str) -> tuple[str | None, str | None]:
    """Return (platform, url) — platform in {'youtube','instagram','spotify'} or None."""
    m = URL_RE.search(text or "")
    url = m.group(0) if m else (text or "").strip()
    if YT_RE.search(url):
        return "youtube", url
    if IG_RE.search(url):
        return "instagram", url
    if SP_RE.search(url):
        return "spotify", url
    return None, None


import shutil


def ffmpeg_path() -> str:
    """System ffmpeg if present, else the binary bundled with imageio-ffmpeg.

    This removes the 'ffmpeg not installed' failure class entirely — the #1
    reason downloads error out before any file exists.
    """
    p = shutil.which("ffmpeg")
    if p:
        return p
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


class Cancelled(Exception):
    """Raised inside a download when the user pressed Cancel."""
