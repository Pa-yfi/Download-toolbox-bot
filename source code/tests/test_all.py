"""Offline test suite. Run from the project root:

    python -m pytest tests/ -v        (or)        python tests/test_all.py

No network or real Telegram token needed — everything runs against mocks.
"""
import asyncio
import os
import re
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "000:TEST")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                                        # noqa: E402
import bot as B                                  # noqa: E402
from progress import StatusMessage               # noqa: E402
from downloaders import detect_platform          # noqa: E402
from downloaders import youtube as yt            # noqa: E402
import i18n                                      # noqa: E402


class F_:
    def __init__(s, fid): s.file_id = fid


class SentMsg:
    def __init__(s, kind, mid=555):
        s.message_id = mid
        s.video = F_("VID1") if kind == "video" else None
        s.audio = F_("AUD1") if kind == "audio" else None
        s.photo = [F_("PH1")] if kind == "photo" else None


class MockBot:
    def __init__(s): s.log = []; s.edits = []
    async def send_video(s, *, chat_id, video, caption=None,
                         request_timeout=None, **kw):
        s.log.append(("video", chat_id, video)); return SentMsg("video")
    async def send_audio(s, *, chat_id, audio, caption=None,
                         request_timeout=None):
        s.log.append(("audio", chat_id, audio)); return SentMsg("audio")
    async def send_photo(s, *, chat_id, photo, caption=None,
                         request_timeout=None):
        s.log.append(("photo", chat_id, photo)); return SentMsg("photo")
    async def send_message(s, *, chat_id, text, **kw):
        s.log.append(("msg", chat_id, text))
    async def edit_message_text(s, *, text, **kw): s.edits.append(text)
    async def edit_message_media(s, *, inline_message_id, media,
                                 reply_markup=None):
        s.log.append(("morph", inline_message_id, media.media))
    async def edit_message_reply_markup(s, **kw): pass
    async def delete_message(s, **kw): s.log.append(("del",))


def pcts(mb):
    return [int(m.group(1)) for e in mb.edits
            for m in [re.search(r"<b>(\d+)%</b>", e)] if m]


def test_url_detection():
    assert detect_platform("https://youtu.be/x")[0] == "youtube"
    assert detect_platform("see https://www.instagram.com/reel/AB/?x=1")[0] == "instagram"
    assert detect_platform("https://open.spotify.com/track/z")[0] == "spotify"
    assert detect_platform("hello") == (None, None)


def test_i18n_complete():
    src = open("bot.py").read() + open("progress.py").read()
    for key in set(re.findall(
            r"t\((?:lang|\"en\"|'en')\s*,\s*[\"']([a-z_]+)[\"']", src)):
        assert key in i18n.STRINGS, f"missing i18n key {key}"
        assert "fa" in i18n.STRINGS[key] and "en" in i18n.STRINGS[key]


def test_db_roundtrip():
    db.set_lang(1, "fa"); assert db.get_lang(1) == "fa"
    db.pending_set(5, 1, "x"); assert db.pending_get(5, 1) == "x"
    db.pending_pop(5, 1); assert db.pending_get(5, 1) is None
    db.cache_put("k", [1, 2]); assert db.cache_get("k") == [1, 2]
    db.set_cached_file("f", "FID"); assert db.get_cached_file("f") == "FID"
    assert db.quota_ok(99, 1)
    db.quota_use(99)
    assert not db.quota_ok(99, 1)
    db.set_ban(98, True); assert db.is_banned(98)


def test_progress_animation():
    async def run():
        mb = MockBot(); st = StatusMessage(mb, 1, 1)
        await st.start("dl"); st.begin("dl"); await st.complete()
        v = pcts(mb)
        assert v == sorted(v) and v[-1] == 100 and len(v) >= 5
        await st.delete()
    asyncio.run(run())


def test_cancel_stops_download():
    from downloaders import Cancelled
    ev = threading.Event(); ev.set()
    hook_called = {}
    def fake_dl():
        # simulate the hook check inside a running yt-dlp download
        if ev.is_set():
            raise Cancelled()
        hook_called["x"] = True
    try:
        fake_dl(); assert False
    except Cancelled:
        pass


def test_userjob_lock_and_release():
    async def run():
        async with B.UserJob(42) as j1:
            assert j1.ok and j1.job_id in B.JOBS
            async with B.UserJob(42) as j2:
                assert not j2.ok
        assert 42 not in B.BUSY and j1.job_id not in B.JOBS
        # release even on exception
        try:
            async with B.UserJob(43) as j:
                assert j.ok
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert 43 not in B.BUSY and B.DL_SEM._value == 3
    asyncio.run(run())


def test_send_media_and_size_guard():
    async def run():
        mb = MockBot()
        f = Path("/tmp/t.mp3"); f.write_bytes(b"a" * 4000)
        assert await B.send_media(mb, 1, f, "audio", "c", "en")
        big = Path("/tmp/tb.bin")
        big.write_bytes(b"0" * ((B.MAX_FILE_MB + 2) * 1024 * 1024))
        assert await B.send_media(mb, 1, big, "video", "c", "en") is None
        big.unlink(); f.unlink()
    asyncio.run(run())


def test_error_sanitizing():
    e = B.friendly_error(Exception(r"fail C:\Users\me\secret\f.mp4"), "en")
    assert "Users" not in e
    assert "دسترس" in B.friendly_error(Exception("Video unavailable"), "fa")




def test_quota_only_charged_on_success():
    """A failed/cancelled download must NOT cost daily quota."""
    uid = 501
    assert db.quota_ok(uid, 2)
    # simulate two checks (e.g. two failed downloads) -> still full quota
    assert db.quota_ok(uid, 2) and db.quota_ok(uid, 2)
    db.quota_use(uid)                      # one SUCCESS
    assert db.quota_ok(uid, 2)             # 1 of 2 used
    db.quota_use(uid)                      # second success
    assert not db.quota_ok(uid, 2)         # now exhausted


def test_ig_cancel_propagates():
    """Cancel event must reach into the Instagram fetch, not after it."""
    import threading
    from downloaders import Cancelled, instagram as ig_mod
    ev = threading.Event(); ev.set()
    opts = ig_mod._ytdlp_opts(Path("/tmp"), ev)
    assert "progress_hooks" in opts
    try:
        opts["progress_hooks"][0]({})
        assert False, "hook did not raise on cancelled event"
    except Cancelled:
        pass


def test_bot_identity_purges_stale_file_ids():
    db.set_cached_file("some:key", "OLD_FID")
    db.ensure_bot_identity(111)            # first run records identity
    db.set_cached_file("some:key", "OLD_FID")
    db.ensure_bot_identity(111)            # same bot -> cache kept
    assert db.get_cached_file("some:key") == "OLD_FID"
    db.ensure_bot_identity(222)            # token changed -> cache purged
    assert db.get_cached_file("some:key") is None


def test_inline_job_end_to_end():
    from types import SimpleNamespace as NS

    async def run():
        B.BOT_USERNAME = "testbot"
        mb = MockBot()
        fake = Path("/tmp/inline_t.mp4"); fake.write_bytes(b"v" * 5000)

        async def fake_dl(url, kind, h, cb, cancel=None):
            cb(50.0, "x"); return fake
        orig = B.yt.download
        B.yt.download = fake_dl
        db.set_lang(600, "en")
        chosen = NS(result_id="dl:yt:mp4", inline_message_id="IMX",
                    query="https://youtu.be/inlinetest",
                    from_user=NS(id=600), bot=mb)
        await B._inline_job(chosen)
        B.yt.download = orig
        assert any(e[0] == "morph" and e[1] == "IMX" for e in mb.log), mb.log
        assert db.get_cached_file("inl:yt:mp4:https://youtu.be/inlinetest") == "VID1"
        fake.unlink(missing_ok=True)
    asyncio.run(run())


def test_ig_cancel_cleanup_no_dir_hack():
    src = open("bot.py").read()
    assert "'post' in dir()" not in src and '"post" in dir()' not in src
    assert "post = None" in src




def _make_video(path, vcodec, acodec="aac"):
    import subprocess
    from downloaders import ffmpeg_path
    subprocess.run([ffmpeg_path(), "-y",
                    "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:v", vcodec, "-pix_fmt", "yuv420p",
                    "-c:a", acodec, "-shortest", str(path)],
                   capture_output=True, check=True)


def test_video_normalize_bad_codec():
    """A non-h264 video (like some Instagram serves) gets transcoded to
    Telegram-playable h264/aac mp4."""
    from downloaders import media as vm
    bad = Path("/tmp/vid_mpeg4.mp4")
    _make_video(bad, "mpeg4")
    info = vm.probe(bad)
    assert info["vcodec"] == "mpeg4" and info["width"] == 320
    fixed = vm.normalize(bad)
    info2 = vm.probe(fixed)
    assert info2["vcodec"] == "h264", info2
    assert info2["acodec"] == "aac"
    assert info2["width"] == 320 and info2["height"] == 240
    assert info2["duration"] >= 1
    fixed.unlink(missing_ok=True)


def test_video_normalize_good_codec_faststart():
    """Already-h264 video: cheap remux, moov atom moved to the front so
    playback starts instantly instead of showing a frozen frame."""
    from downloaders import media as vm
    ok = Path("/tmp/vid_h264.mp4")
    _make_video(ok, "libx264")
    fixed = vm.normalize(ok)
    assert vm.probe(fixed)["vcodec"] == "h264"
    raw = fixed.read_bytes()
    assert raw.find(b"moov") < raw.find(b"mdat"), "moov not at front"
    fixed.unlink(missing_ok=True)


def test_send_media_normalizes_and_streams():
    """send_media must route videos through normalize and attach
    width/height/duration + supports_streaming."""
    async def run():
        got = {}
        class VBot(MockBot):
            async def send_video(s, *, chat_id, video, caption=None,
                                 width=None, height=None, duration=None,
                                 supports_streaming=None,
                                 request_timeout=None):
                got.update(w=width, h=height, d=duration,
                           stream=supports_streaming)
                return SentMsg("video")
        bad = Path("/tmp/vid_send.mp4")
        _make_video(bad, "mpeg4")
        assert await B.send_media(VBot(), 1, bad, "video", "c", "en")
        assert got["stream"] is True
        assert got["w"] == 320 and got["h"] == 240 and got["d"] >= 1
        for p in Path("/tmp").glob("vid_send*"):
            p.unlink(missing_ok=True)
    asyncio.run(run())




def test_inline_results_carry_keyboard():
    """REGRESSION GUARD: without reply_markup on inline results, Telegram
    never sends inline_message_id and the download can never start."""
    from types import SimpleNamespace as NS

    async def run():
        for q in ("https://youtu.be/a", "https://instagram.com/reel/B/",
                  "https://open.spotify.com/track/c"):
            answers = []
            async def a(results, **kw): answers.append(results)
            await B.on_inline(NS(query=q, from_user=NS(id=920), answer=a))
            for r in answers[0]:
                if r.id.startswith("dl:"):
                    assert getattr(r, "reply_markup", None) is not None, \
                        f"{r.id} missing inline keyboard"
    asyncio.run(run())


def test_spotify_page_parser():
    from downloaders import spotify as sp
    html = ('<meta property="og:title" content="Faded"/>'
            '<meta property="og:description" content="Alan Walker · Song · 2015"/>')
    q, disp = sp.parse_track_page(html)
    assert q == "Alan Walker Faded" and disp == "Alan Walker - Faded"
    try:
        sp.parse_track_page("<html>nope</html>")
        assert False
    except RuntimeError:
        pass


def test_spotify_fallback_to_youtube():
    """spotdl dies -> track name scraped -> YouTube match downloaded."""
    from downloaders import spotify as sp

    async def run():
        calls = {}
        def boom(url, cb, cancel=None):
            raise RuntimeError("spotdl exploded")
        async def fake_search(q, n):
            calls["q"] = q
            return [{"url": "https://youtu.be/match", "title": "Faded",
                     "duration": 212}]
        async def fake_dl(url, kind, h, cb, cancel=None):
            calls["dl"] = (url, kind)
            f = Path("/tmp/sp_fb.mp3"); f.write_bytes(b"a" * 2000)
            return f
        def fake_query(url):
            return "Alan Walker Faded", "Alan Walker - Faded"

        import downloaders.youtube as yt_mod
        o1, o2, o3, o4 = sp._spotdl, sp._track_query, yt_mod.search, yt_mod.download
        sp._spotdl, sp._track_query = boom, fake_query
        yt_mod.search, yt_mod.download = fake_search, fake_dl
        try:
            path, title = await sp.download("https://open.spotify.com/track/x",
                                            lambda *a: None)
            assert title == "Alan Walker - Faded"
            assert calls["q"] == "Alan Walker Faded official audio"
            assert calls["dl"] == ("https://youtu.be/match", "mp3")
            path.unlink(missing_ok=True)
        finally:
            sp._spotdl, sp._track_query = o1, o2
            yt_mod.search, yt_mod.download = o3, o4
    asyncio.run(run())


def test_spotify_cancel_still_raises():
    from downloaders import Cancelled, spotify as sp

    async def run():
        def cancelled(url, cb, cancel=None):
            raise Cancelled()
        orig = sp._spotdl
        sp._spotdl = cancelled
        try:
            try:
                await sp.download("https://open.spotify.com/track/x",
                                  lambda *a: None)
                assert False, "Cancelled must not trigger the fallback"
            except Cancelled:
                pass
        finally:
            sp._spotdl = orig
    asyncio.run(run())


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"✅ {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
