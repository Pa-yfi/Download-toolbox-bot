"""Animated status message — single-renderer architecture.

One background task owns ALL edits to the status message. Everything else
(handlers, download threads) only writes plain state fields, which is
thread-safe under the GIL. No races, no starved updates, no frozen bars:
the renderer repaints whatever the current state is every 1.5 seconds.
"""
import asyncio
import random
import time
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

SPINNER = ["⏳", "⌛️"]
DOTS = ["", ".", "..", "..."]
BAR_LEN = 12
TICK = 1.5


def bar(pct: float) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = round(BAR_LEN * pct / 100)
    return "█" * filled + "░" * (BAR_LEN - filled)


class StatusMessage:
    def __init__(self, bot, chat_id: int | None = None,
                 message_id: int | None = None,
                 inline_message_id: str | None = None):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.inline_message_id = inline_message_id
        self.reply_markup = None       # e.g. a ❌ Cancel button
        # ---- state (written from anywhere, incl. threads) ----
        self.mode = "spin"          # "spin" | "pct"
        self.label = ""
        self.pct: float | None = None   # REAL progress (target)
        self.display = 0.0              # ANIMATED progress actually shown
        self.extra = ""
        # ---- renderer internals ----
        self._task: asyncio.Task | None = None
        self._tick = 0
        self._last_text = ""
        self._pause_until = 0.0

    # ------------- state setters (sync + thread-safe) -------------
    def set_spin(self, label: str):
        self.label = label
        self.mode = "spin"

    def set_pct(self, label: str, pct: float | None, extra: str = ""):
        self.label = label
        self.pct = pct
        self.extra = extra
        self.mode = "pct"

    def begin(self, label: str):
        """Start the animated bar even when real progress is unknown."""
        self.set_pct(label, None)

    def _advance(self):
        """Move the displayed bar a random step toward the real target.
        Never exceeds real progress; never moves backwards."""
        cap = self.pct if self.pct is not None else 90.0
        if self.display < cap:
            self.display = min(cap, self.display + random.uniform(7, 16))

    async def complete(self, label: str | None = None):
        """Animate the bar to a full line with quick random jumps —
        guarantees every download visibly fills up, even instant ones."""
        if label:
            self.label = label
        self.mode = "pct"
        self.pct = 100.0
        while self.display < 100.0:
            await self._render(force=True)   # _advance() does the stepping
            await asyncio.sleep(0.55)
        await self._render(force=True)       # final 100% frame

    # ------------- lifecycle -------------
    async def start(self, label: str):
        self.set_spin(label)
        await self._render(force=True)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        try:
            while True:
                await asyncio.sleep(TICK)
                await self._render()
        except asyncio.CancelledError:
            pass

    def _compose(self) -> str:
        self._tick += 1
        icon = SPINNER[self._tick % len(SPINNER)]
        dots = DOTS[self._tick % len(DOTS)]
        if self.mode == "pct":
            self._advance()
            line = (f"⬇️ {self.label} {icon}\n\n"
                    f"{bar(self.display)}  <b>{self.display:.0f}%</b>")
            if self.extra:
                line += f"\n{self.extra}"
            return line
        return f"{icon} {self.label}{dots}"

    async def _render(self, force: bool = False):
        if time.monotonic() < self._pause_until:
            return
        text = self._compose()
        if text == self._last_text and not force:
            return
        try:
            if self.inline_message_id:
                await self.bot.edit_message_text(
                    text=text, inline_message_id=self.inline_message_id,
                    parse_mode="HTML", reply_markup=self.reply_markup)
            else:
                await self.bot.edit_message_text(
                    text=text, chat_id=self.chat_id,
                    message_id=self.message_id, parse_mode="HTML",
                    reply_markup=self.reply_markup)
            self._last_text = text
        except TelegramRetryAfter as e:
            self._pause_until = time.monotonic() + e.retry_after + 1
        except TelegramBadRequest:
            pass
        except Exception:
            pass

    async def _stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def finish(self, text: str):
        """Stop animating and leave a final message (errors/info)."""
        await self._stop()
        try:
            if self.inline_message_id:
                await self.bot.edit_message_text(
                    text=text, inline_message_id=self.inline_message_id,
                    parse_mode="HTML")
            else:
                await self.bot.edit_message_text(
                    text=text, chat_id=self.chat_id,
                    message_id=self.message_id, parse_mode="HTML")
        except Exception:
            pass

    async def delete(self):
        await self._stop()
        if self.inline_message_id:
            return  # inline messages can't be deleted; they get replaced
        try:
            await self.bot.delete_message(chat_id=self.chat_id,
                                          message_id=self.message_id)
        except Exception:
            pass
