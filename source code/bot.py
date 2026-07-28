"""Multi-platform Telegram downloader bot — full edition.

YouTube (search + qualities + mp3) / Instagram (posts, reels, photos,
profiles, stories) / Spotify. Buttons-only UX, animated progress with cancel
button, fa/en, inline mode, group mentions, SQLite persistence (state
survives restarts), per-user daily quota, ban list, admin panel, download
timeouts, optional self-hosted Bot API server for 2GB files.
"""
import asyncio
import html
import logging
import re
import shutil
import threading
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, ChosenInlineResult, FSInputFile,
                           InlineKeyboardButton, InlineKeyboardMarkup,
                           InlineQuery, InlineQueryResultArticle,
                           InlineQueryResultCachedAudio,
                           InlineQueryResultCachedVideo,
                           InputMediaAudio, InputMediaPhoto, InputMediaVideo,
                           InputTextMessageContent, Message)

import db
from config import (ADMIN_ID, BOT_TOKEN, DAILY_LIMIT, DOWNLOAD_DIR,
                    DOWNLOAD_TIMEOUT, INLINE_DUMP_CHAT, LOCAL_API_URL,
                    MAX_FILE_MB)
from downloaders import Cancelled, detect_platform, ffmpeg_path
from downloaders import media as vmedia
from downloaders import youtube as yt
from downloaders import instagram as ig
from downloaders import spotify as sp
from downloaders.instagram import IGAuthNeeded
from i18n import t
from progress import StatusMessage

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

router = Router()
BOT_USERNAME = ""

DL_SEM = asyncio.Semaphore(3)
BUSY: set[int] = set()
JOBS: dict[str, threading.Event] = {}   # job_id -> cancel event

PATH_RE = re.compile(r"([A-Za-z]:)?[\\/][^\s'\"]+")


def esc(x) -> str:
    return html.escape(str(x or ""))


def friendly_error(e: Exception, lang: str) -> str:
    m = str(e)
    low = m.lower()
    if any(k in low for k in ("unavailable", "removed", "private video",
                              "members-only", "age", "geo", "blocked")):
        return t(lang, "err_unavailable")
    m = PATH_RE.sub("", m)
    return t(lang, "error", err=esc(m[:150]))


# --------------------------------------------------------------- keyboards
def lang_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇮🇷 فارسی", callback_data="lang:fa"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en"),
    ]])


def main_menu_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "btn_yt_search"),
                              callback_data="menu:yts")],
        [InlineKeyboardButton(text=t(lang, "btn_ig_story"),
                              callback_data="menu:story")],
    ])


def wait_kb() -> InlineKeyboardMarkup:
    """Attached to inline 'download' results. REQUIRED: Telegram only
    provides inline_message_id (which we need to edit the placeholder)
    when the result message carries an inline keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⏳", callback_data="noop")]])


def stop_kb(lang: str, job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t(lang, "btn_stop"),
                             callback_data=f"stop:{job_id}")]])


def search_kb(lang: str, key: str, results: list) -> InlineKeyboardMarkup:
    rows = []
    for i, r in enumerate(results):
        title = r["title"][:40]
        dur = yt.fmt_duration(r["duration"])
        label = f"{title} · {dur}" if dur else title
        rows.append([InlineKeyboardButton(text=label,
                                          callback_data=f"pick:{key}:{i}")])
    rows.append([InlineKeyboardButton(text=t(lang, "cancel"),
                                      callback_data=f"pick:{key}:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _size_label(mb) -> str:
    if not mb:
        return ""
    warn = "⚠️ " if mb > MAX_FILE_MB else ""
    return f" · {warn}{mb:.0f}MB"


def yt_kb(lang: str, key: str, info: dict) -> InlineKeyboardMarkup:
    rows, row = [], []
    for h in info["heights"]:
        row.append(InlineKeyboardButton(
            text=f"🎬 {h}p{_size_label(info['sizes'].get(h) or info['sizes'].get(str(h)))}",
            callback_data=f"yt:mp4:{h}:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(
        text=t(lang, "mp3") + _size_label(info.get("mp3_size")),
        callback_data=f"yt:mp3:0:{key}")])
    rows.append([InlineKeyboardButton(text=t(lang, "cancel"),
                                      callback_data=f"yt:cancel:0:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --------------------------------------------------------------- helpers
async def gate(msg_or_user, lang: str, bot: Bot, chat_id: int) -> bool:
    """Ban + daily-quota gate. Returns True if allowed."""
    uid = msg_or_user if isinstance(msg_or_user, int) else msg_or_user.id
    if db.is_banned(uid):
        await bot.send_message(chat_id=chat_id, text=t(lang, "banned_msg"))
        return False
    if uid != ADMIN_ID and not db.quota_ok(uid, DAILY_LIMIT):
        await bot.send_message(chat_id=chat_id,
                               text=t(lang, "daily_limit", n=DAILY_LIMIT))
        return False
    return True


async def ensure_lang(msg: Message) -> str | None:
    lang = db.get_lang(msg.from_user.id)
    if lang:
        return lang
    await msg.answer(t("en", "choose_lang"), reply_markup=lang_kb())
    return None


def size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


async def send_media(bot: Bot, chat_id: int, media, kind: str,
                     caption: str, lang: str) -> Message | None:
    meta = {}
    if isinstance(media, Path):
        if kind == "video":
            # fix codecs/container so Telegram actually plays it
            media = await asyncio.to_thread(vmedia.normalize, media)
            meta = await asyncio.to_thread(vmedia.probe, media)
        if size_mb(media) > MAX_FILE_MB:
            await bot.send_message(chat_id=chat_id, text=t(
                lang, "too_big", size=f"{size_mb(media):.0f}",
                limit=MAX_FILE_MB))
            return None
        media = FSInputFile(media)
    cap = caption[:1000]
    if kind == "video":
        return await bot.send_video(
            chat_id=chat_id, video=media, caption=cap,
            width=meta.get("width") or None,
            height=meta.get("height") or None,
            duration=meta.get("duration") or None,
            supports_streaming=True, request_timeout=900)
    if kind == "photo":
        return await bot.send_photo(chat_id=chat_id, photo=media,
                                    caption=cap, request_timeout=900)
    return await bot.send_audio(chat_id=chat_id, audio=media,
                                caption=cap, request_timeout=900)


def extract_file_id(m: Message, kind: str) -> str | None:
    try:
        if kind == "video":
            return m.video.file_id
        if kind == "photo":
            return m.photo[-1].file_id
        return m.audio.file_id
    except Exception:
        return None


def cleanup(path: Path):
    try:
        parent = path.parent
        path.unlink(missing_ok=True)
        if parent != DOWNLOAD_DIR and parent.name.startswith(
                ("yt_", "ig_", "sp_", "st_")):
            shutil.rmtree(parent, ignore_errors=True)
    except OSError:
        pass


def mentioned(msg: Message) -> bool:
    if not msg.text or not BOT_USERNAME:
        return False
    return f"@{BOT_USERNAME.lower()}" in msg.text.lower()


class UserJob:
    """Per-user single-job lock + global slot + cancel event registration."""

    def __init__(self, uid: int):
        self.uid = uid
        self.ok = False
        self.job_id = uuid.uuid4().hex[:10]
        self.cancel = threading.Event()

    async def __aenter__(self):
        if self.uid in BUSY:
            return self
        BUSY.add(self.uid)
        await DL_SEM.acquire()
        JOBS[self.job_id] = self.cancel
        self.ok = True
        return self

    async def __aexit__(self, *exc):
        if self.ok:
            JOBS.pop(self.job_id, None)
            DL_SEM.release()
            BUSY.discard(self.uid)
        return False


async def with_timeout(coro):
    return await asyncio.wait_for(coro, timeout=DOWNLOAD_TIMEOUT)


@router.callback_query(F.data.startswith("stop:"))
async def cb_stop(cb: CallbackQuery):
    job_id = cb.data.split(":")[1]
    ev = JOBS.get(job_id)
    lang = db.get_lang(cb.from_user.id) or "en"
    if ev:
        ev.set()
        await cb.answer(t(lang, "dl_cancelled"))
    else:
        await cb.answer(t(lang, "expired_toast"))


# --------------------------------------------------------------- commands
@router.message(CommandStart())
async def cmd_start(msg: Message):
    lang = db.get_lang(msg.from_user.id)
    if not lang:
        await msg.answer(t("en", "choose_lang"), reply_markup=lang_kb())
    else:
        await msg.answer(t(lang, "start_hint"), reply_markup=main_menu_kb(lang))


@router.message(Command("lang"))
async def cmd_lang(msg: Message):
    await msg.answer(t("en", "choose_lang"), reply_markup=lang_kb())


@router.message(Command("story"))
async def cmd_story(msg: Message):
    lang = await ensure_lang(msg)
    if not lang:
        return
    db.pending_set(msg.chat.id, msg.from_user.id, "story_user")
    await msg.answer(t(lang, "story_ask_user"))


# ---- admin panel ----
def is_admin(uid: int) -> bool:
    return ADMIN_ID and uid == ADMIN_ID


def consume(uid: int):
    """Charge quota — only after a delivery actually succeeded."""
    if uid != ADMIN_ID:
        db.quota_use(uid)


@router.message(Command("stats"))
async def cmd_stats(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    s = db.stats_all()
    users = len(db.all_user_ids())
    await msg.answer(
        f"👥 users: {users}\n⬇️ downloads: {s.get('dl', 0)}\n"
        f"⚡️ cache hits: {s.get('cache_hit', 0)}\n"
        f"❌ errors: {s.get('err', 0)}\n"
        f"🎬 yt: {s.get('yt', 0)} | 📷 ig: {s.get('ig', 0)} | "
        f"🎵 sp: {s.get('sp', 0)}")


@router.message(Command("health"))
async def cmd_health(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    import yt_dlp
    ff = ffmpeg_path()
    await msg.answer(
        f"🩺 ffmpeg: {'✅' if ff else '❌'}\n"
        f"📦 yt-dlp: {yt_dlp.version.__version__}\n"
        f"📷 IG login: {'✅' if ig.stories_enabled() else '❌ (stories off)'}\n"
        f"🖥 local API: {'✅ ' + LOCAL_API_URL if LOCAL_API_URL else '— (50MB cap)'}\n"
        f"📤 max file: {MAX_FILE_MB}MB | ⏱ timeout: {DOWNLOAD_TIMEOUT}s\n"
        f"🎟 daily limit: {DAILY_LIMIT}/user")


@router.message(Command("ban"))
async def cmd_ban(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    try:
        uid = int(msg.text.split()[1])
        db.set_ban(uid, True)
        await msg.answer(f"⛔️ banned {uid}")
    except (IndexError, ValueError):
        await msg.answer("usage: /ban <user_id>")


@router.message(Command("unban"))
async def cmd_unban(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    try:
        uid = int(msg.text.split()[1])
        db.set_ban(uid, False)
        await msg.answer(f"✅ unbanned {uid}")
    except (IndexError, ValueError):
        await msg.answer("usage: /unban <user_id>")


@router.message(Command("broadcast"))
async def cmd_broadcast(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    text = msg.text.split(maxsplit=1)
    if len(text) < 2:
        await msg.answer("usage: /broadcast <message>")
        return
    uids = db.all_user_ids()
    await msg.answer(f"📣 broadcasting to {len(uids)} users in background...")

    async def _bg():
        sent = 0
        for uid in uids:
            try:
                await msg.bot.send_message(chat_id=uid, text=text[1])
                sent += 1
            except Exception:
                pass
            await asyncio.sleep(0.05)
        try:
            await msg.bot.send_message(chat_id=msg.chat.id,
                                       text=f"📣 done: {sent}/{len(uids)}")
        except Exception:
            pass

    asyncio.create_task(_bg())


@router.callback_query(F.data.startswith("lang:"))
async def cb_lang(cb: CallbackQuery):
    lang = cb.data.split(":")[1]
    db.set_lang(cb.from_user.id, lang)
    await cb.message.edit_text(t(lang, "lang_set"),
                               reply_markup=main_menu_kb(lang))
    await cb.answer()


@router.callback_query(F.data.startswith("menu:"))
async def cb_menu(cb: CallbackQuery):
    action = cb.data.split(":")[1]
    lang = db.get_lang(cb.from_user.id) or "en"
    await cb.answer()
    if action == "yts":
        db.pending_set(cb.message.chat.id, cb.from_user.id, "yt_search")
        await cb.message.answer(t(lang, "yt_search_ask"))
    elif action == "story":
        db.pending_set(cb.message.chat.id, cb.from_user.id, "story_user")
        await cb.message.answer(t(lang, "story_ask_user"))


# --------------------------------------------------------------- youtube
async def flow_youtube(bot: Bot, msg: Message, lang: str, url: str):
    status_msg = await msg.answer(f"⏳ {t(lang, 'searching')}...")
    st = StatusMessage(bot, msg.chat.id, status_msg.message_id)
    await st.start(t(lang, "searching"))
    try:
        info = await with_timeout(yt.probe(url))
    except Exception as e:
        db.stat_inc("err")
        await st.finish(friendly_error(e, lang))
        return
    key = uuid.uuid4().hex[:10]
    db.cache_put(f"url:{key}", url)
    await st.finish(t(lang, "yt_choose", title=esc(info["title"][:120])))
    try:
        await bot.edit_message_reply_markup(
            chat_id=msg.chat.id, message_id=status_msg.message_id,
            reply_markup=yt_kb(lang, key, info))
    except Exception:
        pass


@router.callback_query(F.data.startswith("yt:"))
async def cb_youtube(cb: CallbackQuery):
    _, kind, height, key = cb.data.split(":")
    lang = db.get_lang(cb.from_user.id) or "en"
    if kind == "cancel":
        db.cache_pop(f"url:{key}")
        await cb.answer()
        try:
            await cb.message.edit_text(t(lang, "cancelled"))
        except Exception:
            pass
        return
    url = db.cache_get(f"url:{key}")
    if not url:
        await cb.answer(t(lang, "expired_toast"))
        return
    if cb.from_user.id in BUSY:
        await cb.answer(t(lang, "busy"), show_alert=True)
        return
    db.cache_pop(f"url:{key}")
    await cb.answer()

    bot = cb.bot
    chat_id = cb.message.chat.id
    st = StatusMessage(bot, chat_id, cb.message.message_id)
    if not await gate(cb.from_user.id, lang, bot, chat_id):
        await st.delete()
        return

    cache_key = f"yt:{kind}:{height}:{url}"
    fid = db.get_cached_file(cache_key)
    if fid:
        try:
            await send_media(bot, chat_id, fid,
                             "audio" if kind == "mp3" else "video",
                             t(lang, "done_caption_yt", title="",
                               bot=BOT_USERNAME), lang)
            db.stat_inc("cache_hit")
            consume(cb.from_user.id)
            await st.delete()
            return
        except Exception:
            pass

    async with UserJob(cb.from_user.id) as job:
        if not job.ok:
            await cb.answer(t(lang, "busy"), show_alert=True)
            return
        st.reply_markup = stop_kb(lang, job.job_id)
        await st.start(t(lang, "downloading"))
        label = t(lang, "downloading")

        def on_progress(pct, extra):
            st.set_pct(label, pct, extra)

        try:
            st.begin(label)
            path = await with_timeout(
                yt.download(url, kind, int(height), on_progress, job.cancel))
            await st.complete(label)
            st.reply_markup = None
            st.set_spin(t(lang, "uploading"))
            sent = await send_media(
                bot, chat_id, path, "audio" if kind == "mp3" else "video",
                t(lang, "done_caption_yt", title=esc(path.stem),
                  bot=BOT_USERNAME), lang)
            if sent:
                f = extract_file_id(sent, "audio" if kind == "mp3" else "video")
                if f:
                    db.set_cached_file(cache_key, f)
            cleanup(path)
            db.stat_inc("dl")
            db.stat_inc("yt")
            consume(cb.from_user.id)
            await st.delete()
        except Cancelled:
            await st.finish(t(lang, "dl_cancelled"))
        except asyncio.TimeoutError:
            job.cancel.set()
            await st.finish(t(lang, "timeout"))
        except Exception as e:
            db.stat_inc("err")
            log.exception("youtube download failed")
            await st.finish(friendly_error(e, lang))


async def flow_yt_search(bot: Bot, msg: Message, lang: str, query: str):
    status_msg = await msg.answer(f"⏳ {t(lang, 'yt_searching')}...")
    st = StatusMessage(bot, msg.chat.id, status_msg.message_id)
    await st.start(t(lang, "yt_searching"))
    try:
        results = await with_timeout(yt.search(query, 20))
    except Exception as e:
        db.stat_inc("err")
        await st.finish(friendly_error(e, lang))
        return
    if not results:
        await st.finish(t(lang, "yt_search_none"))
        return
    key = uuid.uuid4().hex[:10]
    db.cache_put(f"srch:{key}", results)
    await st.finish(t(lang, "yt_pick", q=esc(query[:60]), list="").strip())
    try:
        await bot.edit_message_reply_markup(
            chat_id=msg.chat.id, message_id=status_msg.message_id,
            reply_markup=search_kb(lang, key, results))
    except Exception:
        pass


@router.callback_query(F.data.startswith("pick:"))
async def cb_pick(cb: CallbackQuery):
    _, key, idx = cb.data.split(":")
    lang = db.get_lang(cb.from_user.id) or "en"
    if idx == "cancel":
        db.cache_pop(f"srch:{key}")
        await cb.answer()
        try:
            await cb.message.edit_text(t(lang, "cancelled"))
        except Exception:
            pass
        return
    results = db.cache_get(f"srch:{key}")
    if not results:
        await cb.answer(t(lang, "expired_toast"))
        return
    await cb.answer()
    chosen = results[int(idx)]
    db.cache_pop(f"srch:{key}")
    bot = cb.bot
    st = StatusMessage(bot, cb.message.chat.id, cb.message.message_id)
    await st.start(t(lang, "searching"))
    try:
        info = await with_timeout(yt.probe(chosen["url"]))
    except Exception as e:
        db.stat_inc("err")
        await st.finish(friendly_error(e, lang))
        return
    dl_key = uuid.uuid4().hex[:10]
    db.cache_put(f"url:{dl_key}", chosen["url"])
    await st.finish(t(lang, "yt_choose", title=esc(info["title"][:120])))
    try:
        await bot.edit_message_reply_markup(
            chat_id=cb.message.chat.id, message_id=cb.message.message_id,
            reply_markup=yt_kb(lang, dl_key, info))
    except Exception:
        pass


# --------------------------------------------------------------- instagram
async def flow_instagram(bot: Bot, msg: Message, lang: str, url: str):
    if msg.from_user.id in BUSY:
        await msg.answer(t(lang, "busy"))
        return
    if not await gate(msg.from_user, lang, bot, msg.chat.id):
        return
    status_msg = await msg.answer(f"⏳ {t(lang, 'searching')}...")
    st = StatusMessage(bot, msg.chat.id, status_msg.message_id)
    async with UserJob(msg.from_user.id) as job:
        if not job.ok:
            await st.finish(t(lang, "busy"))
            return
        st.reply_markup = stop_kb(lang, job.job_id)
        await st.start(t(lang, "downloading"))
        st.begin(t(lang, "downloading"))
        post = None
        try:
            post = await with_timeout(ig.fetch_post(url, job.cancel))
            await st.complete(t(lang, "downloading"))
            st.reply_markup = None
            st.set_spin(t(lang, "uploading"))
            caption = t(lang, "done_caption_ig",
                        caption=esc(post["caption"]) or "Instagram",
                        bot=BOT_USERNAME)
            sent_any = False
            for path, kind in post["items"]:
                if await send_media(bot, msg.chat.id, path, kind,
                                    caption if not sent_any else "", lang):
                    sent_any = True
                cleanup(path)
            db.stat_inc("dl")
            db.stat_inc("ig")
            consume(msg.from_user.id)
            await st.delete()
        except Cancelled:
            if post:
                for path, _kind in post.get("items", []):
                    cleanup(path)
            await st.finish(t(lang, "dl_cancelled"))
        except IGAuthNeeded:
            await st.finish(t(lang, "ig_auth_needed"))
        except asyncio.TimeoutError:
            await st.finish(t(lang, "timeout"))
        except Exception as e:
            db.stat_inc("err")
            log.exception("instagram failed")
            await st.finish(friendly_error(e, lang))


async def flow_story(bot: Bot, msg: Message, lang: str, username: str):
    username = username.strip().lstrip("@")
    if msg.from_user.id in BUSY:
        await msg.answer(t(lang, "busy"))
        return
    if not await gate(msg.from_user, lang, bot, msg.chat.id):
        return
    status_msg = await msg.answer(
        f"⏳ {t(lang, 'story_searching', user=esc(username))}")
    st = StatusMessage(bot, msg.chat.id, status_msg.message_id)
    async with UserJob(msg.from_user.id) as job:
        if not job.ok:
            await st.finish(t(lang, "busy"))
            return
        st.reply_markup = stop_kb(lang, job.job_id)
        await st.start(t(lang, "story_searching", user=esc(username)))
        try:
            prof = await with_timeout(ig.profile(username))
            await bot.send_message(
                chat_id=msg.chat.id,
                text=t(lang, "profile_info", full=esc(prof["full_name"]),
                       user=esc(prof["username"]),
                       followers=f"{prof['followers']:,}",
                       posts=prof["posts"], bio=esc(prof["bio"])))
            if not ig.stories_enabled():
                await st.finish(t(lang, "story_disabled"))
                return
            st.begin(t(lang, "downloading"))
            items = await with_timeout(
                ig.stories(prof["userid"], job.cancel))
            await st.complete(t(lang, "downloading"))
            if not items:
                await st.finish(t(lang, "story_none", user=esc(username)))
                return
            st.reply_markup = None
            st.set_spin(t(lang, "sending_stories", n=len(items)))
            for path, kind in items:
                await send_media(bot, msg.chat.id, path, kind, "", lang)
                cleanup(path)
            db.stat_inc("dl")
            db.stat_inc("ig")
            consume(msg.from_user.id)
            await st.delete()
        except Cancelled:
            await st.finish(t(lang, "dl_cancelled"))
        except IGAuthNeeded:
            await st.finish(t(lang, "ig_auth_needed"))
        except asyncio.TimeoutError:
            await st.finish(t(lang, "timeout"))
        except Exception as e:
            db.stat_inc("err")
            log.exception("story failed")
            await st.finish(friendly_error(e, lang))


# --------------------------------------------------------------- spotify
async def flow_spotify(bot: Bot, msg: Message, lang: str, url: str):
    if msg.from_user.id in BUSY:
        await msg.answer(t(lang, "busy"))
        return
    if not await gate(msg.from_user, lang, bot, msg.chat.id):
        return
    status_msg = await msg.answer(f"⏳ {t(lang, 'searching')}...")
    st = StatusMessage(bot, msg.chat.id, status_msg.message_id)

    fid = db.get_cached_file(f"sp:{url}")
    if fid:
        try:
            await send_media(bot, msg.chat.id, fid, "audio",
                             t(lang, "done_caption_sp", title="",
                               bot=BOT_USERNAME), lang)
            db.stat_inc("cache_hit")
            consume(msg.from_user.id)
            await st.delete()
            return
        except Exception:
            pass

    async with UserJob(msg.from_user.id) as job:
        if not job.ok:
            await st.finish(t(lang, "busy"))
            return
        st.reply_markup = stop_kb(lang, job.job_id)
        await st.start(t(lang, "downloading"))
        label = t(lang, "downloading")

        def on_progress(pct, extra):
            st.set_pct(label, pct, extra)

        try:
            st.begin(label)
            path, title = await with_timeout(
                sp.download(url, on_progress, job.cancel))
            await st.complete(label)
            st.reply_markup = None
            st.set_spin(t(lang, "uploading"))
            sent = await send_media(bot, msg.chat.id, path, "audio",
                                    t(lang, "done_caption_sp",
                                      title=esc(title), bot=BOT_USERNAME),
                                    lang)
            if sent:
                f = extract_file_id(sent, "audio")
                if f:
                    db.set_cached_file(f"sp:{url}", f)
            cleanup(path)
            db.stat_inc("dl")
            db.stat_inc("sp")
            consume(msg.from_user.id)
            await st.delete()
        except Cancelled:
            await st.finish(t(lang, "dl_cancelled"))
        except asyncio.TimeoutError:
            job.cancel.set()
            await st.finish(t(lang, "timeout"))
        except Exception as e:
            db.stat_inc("err")
            log.exception("spotify failed")
            await st.finish(friendly_error(e, lang))


# --------------------------------------------------------------- inline mode
async def _upload_for_inline(bot: Bot, user_id: int, path: Path,
                             kind: str) -> str:
    dump = int(INLINE_DUMP_CHAT) if INLINE_DUMP_CHAT else user_id
    sent = await send_media(bot, dump, path, kind, "", "en")
    if not sent:
        raise RuntimeError("upload failed (file too big)")
    fid = extract_file_id(sent, kind)
    try:
        await bot.delete_message(chat_id=dump, message_id=sent.message_id)
    except Exception:
        pass
    if not fid:
        raise RuntimeError("no file_id from upload")
    return fid


@router.inline_query()
async def on_inline(q: InlineQuery):
    lang = db.get_lang(q.from_user.id) or "en"
    platform, url = detect_platform(q.query or "")
    results = []
    if not platform:
        results.append(InlineQueryResultArticle(
            id="help", title=t(lang, "inl_help_title"),
            description=t(lang, "inl_help_desc"),
            input_message_content=InputTextMessageContent(
                message_text=t(lang, "inl_help_desc"))))
        await q.answer(results, cache_time=5, is_personal=True)
        return
    wait = InputTextMessageContent(message_text=t(lang, "inl_wait"))
    if platform == "youtube":
        fid_v = db.get_cached_file(f"inl:yt:mp4:{url}")
        fid_a = db.get_cached_file(f"inl:yt:mp3:{url}")
        if fid_v:
            results.append(InlineQueryResultCachedVideo(
                id="c:yt:mp4", video_file_id=fid_v,
                title=t(lang, "inl_cached") + " 🎬",
                caption=f"🤖 @{BOT_USERNAME}"))
        if fid_a:
            results.append(InlineQueryResultCachedAudio(
                id="c:yt:mp3", audio_file_id=fid_a,
                caption=f"🤖 @{BOT_USERNAME}"))
        results.append(InlineQueryResultArticle(
            id="dl:yt:mp4", title=t(lang, "inl_video"),
            description=url[:60], input_message_content=wait,
            reply_markup=wait_kb()))
        results.append(InlineQueryResultArticle(
            id="dl:yt:mp3", title=t(lang, "inl_mp3"),
            description=url[:60], input_message_content=wait,
            reply_markup=wait_kb()))
    elif platform == "instagram":
        fid = db.get_cached_file(f"inl:ig:{url}")
        if fid:
            results.append(InlineQueryResultCachedVideo(
                id="c:ig", video_file_id=fid,
                title=t(lang, "inl_cached"),
                caption=f"🤖 @{BOT_USERNAME}"))
        results.append(InlineQueryResultArticle(
            id="dl:ig", title=t(lang, "inl_media"),
            description=url[:60], input_message_content=wait,
            reply_markup=wait_kb()))
    else:
        fid = db.get_cached_file(f"sp:{url}")
        if fid:
            results.append(InlineQueryResultCachedAudio(
                id="c:sp", audio_file_id=fid,
                caption=f"🤖 @{BOT_USERNAME}"))
        results.append(InlineQueryResultArticle(
            id="dl:sp", title=t(lang, "inl_media"),
            description=url[:60], input_message_content=wait,
            reply_markup=wait_kb()))
    await q.answer(results, cache_time=1, is_personal=True)


@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


@router.chosen_inline_result()
async def on_chosen(chosen: ChosenInlineResult):
    log.info("chosen inline result: %s (has_msg_id=%s)",
             chosen.result_id, bool(chosen.inline_message_id))
    if not chosen.result_id.startswith("dl:") or not chosen.inline_message_id:
        return
    asyncio.create_task(_inline_job(chosen))


async def _inline_job(chosen: ChosenInlineResult):
    bot: Bot = chosen.bot
    lang = db.get_lang(chosen.from_user.id) or "en"
    _, plat, *rest = chosen.result_id.split(":")
    fmt = rest[0] if rest else ""
    _, url = detect_platform(chosen.query or "")
    imid = chosen.inline_message_id
    st = StatusMessage(bot, inline_message_id=imid)

    if db.is_banned(chosen.from_user.id):
        await st.finish(t(lang, "banned_msg"))
        return
    if (chosen.from_user.id != ADMIN_ID
            and not db.quota_ok(chosen.from_user.id, DAILY_LIMIT)):
        await st.finish(t(lang, "daily_limit", n=DAILY_LIMIT))
        return

    async with UserJob(chosen.from_user.id) as job:
        if not job.ok:
            await st.finish(t(lang, "busy"))
            return
        st.reply_markup = stop_kb(lang, job.job_id)
        await st.start(t(lang, "downloading"))
        label = t(lang, "downloading")

        def on_progress(pct, extra):
            st.set_pct(label, pct, extra)

        path = None
        extras: list = []
        try:
            if plat == "yt" and fmt == "mp3":
                st.begin(label)
                path = await with_timeout(
                    yt.download(url, "mp3", 0, on_progress, job.cancel))
                await st.complete(label)
                kind, cache_key = "audio", f"inl:yt:mp3:{url}"
            elif plat == "yt":
                path, kind, cache_key = None, "video", f"inl:yt:mp4:{url}"
                st.begin(label)
                for h in (720, 480, 360):
                    p = await with_timeout(
                        yt.download(url, "mp4", h, on_progress, job.cancel))
                    if size_mb(p) <= MAX_FILE_MB:
                        path = p
                        break
                    cleanup(p)
                if not path:
                    raise RuntimeError("video too large for Telegram bots")
                await st.complete(label)
            elif plat == "ig":
                st.begin(label)
                post = await with_timeout(ig.fetch_post(url, job.cancel))
                await st.complete(label)
                if not post["items"]:
                    raise RuntimeError("no media found")
                path, kind = post["items"][0]
                extras = post["items"][1:]
                cache_key = f"inl:ig:{url}"
                kind = "video" if kind == "video" else "photo"
            else:
                st.begin(label)
                path, _title = await with_timeout(
                    sp.download(url, on_progress, job.cancel))
                await st.complete(label)
                kind, cache_key = "audio", f"sp:{url}"

            st.reply_markup = None
            st.set_spin(t(lang, "uploading"))
            fid = await _upload_for_inline(bot, chosen.from_user.id, path, kind)
            db.set_cached_file(cache_key, fid)
            await st._stop()
            cap = f"🤖 @{BOT_USERNAME}"
            media = (InputMediaVideo(media=fid, caption=cap) if kind == "video"
                     else InputMediaPhoto(media=fid, caption=cap)
                     if kind == "photo"
                     else InputMediaAudio(media=fid, caption=cap))
            await bot.edit_message_media(inline_message_id=imid,
                                         media=media,
                                         reply_markup=None)
            # carousel remainder -> user's PM so nothing is lost
            for extra_path, extra_kind in extras:
                try:
                    await send_media(bot, chosen.from_user.id, extra_path,
                                     extra_kind, "", lang)
                except Exception:
                    pass
                cleanup(extra_path)
            db.stat_inc("dl")
            db.stat_inc(plat if plat != "yt" else "yt")
            consume(chosen.from_user.id)
        except Cancelled:
            await st.finish(t(lang, "dl_cancelled"))
        except IGAuthNeeded:
            await st.finish(t(lang, "ig_auth_needed"))
        except asyncio.TimeoutError:
            job.cancel.set()
            await st.finish(t(lang, "timeout"))
        except Exception as e:
            db.stat_inc("err")
            log.exception("inline job failed")
            msg_txt = friendly_error(e, lang)
            if ("chat not found" in str(e).lower()
                    or "blocked" in str(e).lower()):
                msg_txt = t(lang, "inl_need_start", bot=BOT_USERNAME)
            await st.finish(msg_txt)
        finally:
            if path:
                cleanup(path)


# --------------------------------------------------------------- messages
@router.message(F.text)
async def on_text(msg: Message):
    bot = msg.bot
    is_group = msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    chat, uid = msg.chat.id, msg.from_user.id
    pending = db.pending_get(chat, uid)

    if mentioned(msg):
        stripped = msg.text.replace(f"@{BOT_USERNAME}", "").strip()
        platform, url = detect_platform(stripped)
        lang = db.get_lang(uid)
        if not lang:
            await msg.answer(t("en", "choose_lang"), reply_markup=lang_kb())
            db.pending_set(chat, uid, "link")
            return
        if platform:
            db.pending_pop(chat, uid)
            return await dispatch(bot, msg, lang, platform, url)
        db.pending_set(chat, uid, "link")
        await msg.answer(t(lang, "ask_link"))
        return

    if is_group and not pending:
        return

    lang = await ensure_lang(msg)
    if not lang:
        if pending != "story_user":
            db.pending_set(chat, uid, "link")
        return

    if pending == "story_user":
        db.pending_pop(chat, uid)
        return await flow_story(bot, msg, lang, msg.text)

    if pending == "yt_search":
        db.pending_pop(chat, uid)
        return await flow_yt_search(bot, msg, lang, msg.text.strip())

    platform, url = detect_platform(msg.text)
    if not platform:
        if pending == "link" or not is_group:
            await msg.answer(t(lang, "bad_link"),
                             reply_markup=main_menu_kb(lang))
        return
    db.pending_pop(chat, uid)
    await dispatch(bot, msg, lang, platform, url)


async def dispatch(bot: Bot, msg: Message, lang: str, platform: str, url: str):
    if platform == "youtube":
        await flow_youtube(bot, msg, lang, url)
    elif platform == "instagram":
        await flow_instagram(bot, msg, lang, url)
    elif platform == "spotify":
        await flow_spotify(bot, msg, lang, url)


# --------------------------------------------------------------- startup
def sweep_downloads():
    for p in DOWNLOAD_DIR.glob("*"):
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(
            missing_ok=True)


def make_bot() -> Bot:
    kwargs = {"default": DefaultBotProperties(parse_mode=ParseMode.HTML)}
    if LOCAL_API_URL:
        kwargs["session"] = AiohttpSession(
            api=TelegramAPIServer.from_base(LOCAL_API_URL))
    return Bot(BOT_TOKEN, **kwargs)


async def main():
    global BOT_USERNAME
    sweep_downloads()
    bot = make_bot()
    dp = Dispatcher()
    dp.include_router(router)
    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError:
        log.error("BOT_TOKEN is invalid or revoked — fix it in .env "
                  "(get the current token from @BotFather -> /mybots)")
        await bot.session.close()
        raise SystemExit(0)   # exit code 0: runner will NOT restart-loop
    BOT_USERNAME = me.username
    db.ensure_bot_identity(me.id)   # token changed -> stale file_ids purged
    log.info("Started as @%s (max file %sMB%s)", BOT_USERNAME, MAX_FILE_MB,
             ", local API" if LOCAL_API_URL else "")

    async def _updater():
        import subprocess, sys
        while True:
            await asyncio.sleep(24 * 3600)
            log.info("refreshing yt-dlp (takes effect on next restart)")
            await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp"],
            )

    asyncio.create_task(_updater())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
