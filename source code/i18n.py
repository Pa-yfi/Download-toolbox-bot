"""Two-language string table. t(lang, key, **fmt) returns the string."""

STRINGS = {
    "choose_lang": {
        "fa": "🌐 لطفاً زبان خود را انتخاب کنید:",
        "en": "🌐 Please choose your language:",
    },
    "lang_set": {
        "fa": "✅ زبان فارسی انتخاب شد.\n\nیک لینک از یوتیوب، اینستاگرام یا اسپاتیفای بفرستید 🎬🎵📷",
        "en": "✅ English selected.\n\nSend me a link from YouTube, Instagram or Spotify 🎬🎵📷",
    },
    "start_hint": {
        "fa": "یک لینک از یوتیوب، اینستاگرام یا اسپاتیفای بفرستید.\nبرای جستجوی کاربر اینستاگرام: /story",
        "en": "Send a YouTube, Instagram or Spotify link.\nTo look up an Instagram user's story: /story",
    },
    "ask_link": {
        "fa": "🔗 لینک خود را ارسال کنید (یوتیوب / اینستاگرام / اسپاتیفای):",
        "en": "🔗 Send me your link (YouTube / Instagram / Spotify):",
    },
    "bad_link": {
        "fa": "❌ این لینک پشتیبانی نمی‌شود. لینک یوتیوب، اینستاگرام یا اسپاتیفای بفرستید.",
        "en": "❌ Unsupported link. Send a YouTube, Instagram or Spotify link.",
    },
    "searching": {
        "fa": "🔎 در حال بررسی لینک شما",
        "en": "🔎 Looking up your link",
    },
    "yt_choose": {
        "fa": "🎬 <b>{title}</b>\n\nفرمت و کیفیت را انتخاب کنید:",
        "en": "🎬 <b>{title}</b>\n\nChoose a format and quality:",
    },
    "downloading": {
        "fa": "⬇️ در حال دانلود",
        "en": "⬇️ Downloading",
    },
    "processing": {
        "fa": "⚙️ در حال پردازش فایل",
        "en": "⚙️ Processing file",
    },
    "uploading": {
        "fa": "📤 در حال ارسال",
        "en": "📤 Uploading",
    },
    "done_caption_yt": {
        "fa": "🎬 {title}\n\n🤖 @{bot}",
        "en": "🎬 {title}\n\n🤖 @{bot}",
    },
    "done_caption_ig": {
        "fa": "📷 {caption}\n\n🤖 @{bot}",
        "en": "📷 {caption}\n\n🤖 @{bot}",
    },
    "done_caption_sp": {
        "fa": "🎵 {title}\n\n🤖 @{bot}",
        "en": "🎵 {title}\n\n🤖 @{bot}",
    },
    "too_big": {
        "fa": "❌ حجم فایل ({size} مگابایت) بیشتر از حد مجاز تلگرام ({limit} مگابایت) است. کیفیت پایین‌تر را امتحان کنید.",
        "en": "❌ File is {size} MB which exceeds Telegram's {limit} MB bot limit. Try a lower quality.",
    },
    "error": {
        "fa": "❌ خطا: {err}",
        "en": "❌ Error: {err}",
    },
    "story_ask_user": {
        "fa": "👤 نام کاربری اینستاگرام را بفرستید (بدون @):",
        "en": "👤 Send the Instagram username (without @):",
    },
    "story_searching": {
        "fa": "🔎 در حال جستجوی کاربر {user}",
        "en": "🔎 Searching for user {user}",
    },
    "story_none": {
        "fa": "ℹ️ کاربر {user} در حال حاضر استوری فعالی ندارد.",
        "en": "ℹ️ {user} has no active stories right now.",
    },
    "story_disabled": {
        "fa": "⚠️ قابلیت استوری فعال نیست (لاگین اینستاگرام در .env تنظیم نشده).",
        "en": "⚠️ Story lookup is disabled (no Instagram login set in .env).",
    },
    "profile_info": {
        "fa": "👤 <b>{full}</b> (@{user})\n👥 {followers} دنبال‌کننده | 📸 {posts} پست\n{bio}",
        "en": "👤 <b>{full}</b> (@{user})\n👥 {followers} followers | 📸 {posts} posts\n{bio}",
    },
    "sending_stories": {
        "fa": "📤 در حال ارسال {n} استوری",
        "en": "📤 Sending {n} stories",
    },
    "mp4": {"fa": "🎬 ویدیو MP4", "en": "🎬 Video MP4"},
    "mp3": {"fa": "🎵 صدا MP3", "en": "🎵 Audio MP3"},
    "cancel": {"fa": "❌ لغو", "en": "❌ Cancel"},
    "cancelled": {"fa": "لغو شد.", "en": "Cancelled."},
}


def t(lang: str, key: str, **fmt) -> str:
    lang = lang if lang in ("fa", "en") else "en"
    s = STRINGS.get(key, {}).get(lang, key)
    return s.format(**fmt) if fmt else s

STRINGS.update({
    "menu": {
        "fa": "چه کاری برایتان انجام دهم؟ می‌توانید مستقیم لینک هم بفرستید.",
        "en": "What would you like to do? You can also just send a link directly.",
    },
    "btn_yt_search": {"fa": "🔎 جستجوی یوتیوب", "en": "🔎 YouTube search"},
    "btn_ig_story": {"fa": "📷 استوری اینستاگرام", "en": "📷 Instagram story"},
    "yt_search_ask": {
        "fa": "🎵 نام آهنگ یا خواننده را بفرستید:",
        "en": "🎵 Send the song or artist name:",
    },
    "yt_searching": {
        "fa": "🔎 در حال جستجو در یوتیوب",
        "en": "🔎 Searching YouTube",
    },
    "yt_search_none": {
        "fa": "❌ نتیجه‌ای پیدا نشد. عبارت دیگری امتحان کنید.",
        "en": "❌ No results found. Try a different search.",
    },
    "yt_pick": {
        "fa": "🔎 نتایج برای «{q}» — یکی را انتخاب کنید:\n\n{list}",
        "en": "🔎 Results for \"{q}\" — pick one:\n\n{list}",
    },
    "expired": {
        "fa": "⌛️ این جستجو منقضی شده. دوباره جستجو کنید.",
        "en": "⌛️ This search expired. Please search again.",
    },
})

STRINGS.update({
    "ig_auth_needed": {
        "fa": ("⚠️ اینستاگرام درخواست‌های بدون لاگین را مسدود می‌کند (خطای 429).\n\n"
               "راه حل: در فایل .env یکی از این‌ها را تنظیم کنید:\n"
               "• COOKIES_FROM_BROWSER=firefox (در مرورگر وارد اینستاگرام باشید)\n"
               "• یا IG_COOKIES_FILE به یک فایل cookies.txt\n"
               "• برای استوری: IG_SESSION_FILE (با دستور instaloader --login)\n\n"
               "سپس ربات را ری‌استارت کنید."),
        "en": ("⚠️ Instagram is blocking anonymous requests (429 rate limit).\n\n"
               "Fix: set one of these in your .env file:\n"
               "• COOKIES_FROM_BROWSER=firefox (be logged into IG in that browser)\n"
               "• or IG_COOKIES_FILE pointing to an exported cookies.txt\n"
               "• for stories: IG_SESSION_FILE (create with: instaloader --login)\n\n"
               "Then restart the bot."),
    },
    "timeout": {
        "fa": "⌛️ عملیات بیش از حد طول کشید و لغو شد. دوباره تلاش کنید.",
        "en": "⌛️ The operation took too long and was cancelled. Try again.",
    },
})

STRINGS.update({
    "busy": {
        "fa": "⏳ یک دانلود شما در حال انجام است — صبر کنید تا تمام شود.",
        "en": "⏳ You already have a download in progress — wait for it to finish.",
    },
    "queued": {
        "fa": "🕐 در صف دانلود",
        "en": "🕐 In download queue",
    },
    "err_unavailable": {
        "fa": "❌ این ویدیو در دسترس نیست (حذف شده، خصوصی یا محدود شده است).",
        "en": "❌ This video is unavailable (removed, private, or restricted).",
    },
    "expired_toast": {
        "fa": "منقضی شده — دوباره جستجو کنید",
        "en": "Expired — search again",
    },
})

STRINGS.update({
    "inl_help_title": {"fa": "لینک بفرستید", "en": "Paste a link"},
    "inl_help_desc": {
        "fa": "لینک یوتیوب، اینستاگرام یا اسپاتیفای را بعد از نام ربات بنویسید",
        "en": "Type a YouTube, Instagram or Spotify link after the bot name",
    },
    "inl_video": {"fa": "🎬 دانلود ویدیو و ارسال در همین چت",
                  "en": "🎬 Download video & send here"},
    "inl_mp3": {"fa": "🎵 دانلود MP3 و ارسال در همین چت",
                "en": "🎵 Download MP3 & send here"},
    "inl_media": {"fa": "📥 دانلود و ارسال در همین چت",
                  "en": "📥 Download & send here"},
    "inl_cached": {"fa": "⚡️ ارسال فوری (از قبل آماده)",
                   "en": "⚡️ Send instantly (cached)"},
    "inl_wait": {"fa": "⏳ در حال آماده‌سازی، چند لحظه صبر کنید...",
                 "en": "⏳ Preparing your media, one moment..."},
    "inl_need_start": {
        "fa": "❌ ابتدا ربات را استارت کنید: @{bot} سپس دوباره تلاش کنید.",
        "en": "❌ Please press Start on @{bot} first, then try again.",
    },
})

STRINGS.update({
    "btn_stop": {"fa": "❌ لغو دانلود", "en": "❌ Cancel download"},
    "dl_cancelled": {"fa": "❌ دانلود لغو شد.", "en": "❌ Download cancelled."},
    "daily_limit": {
        "fa": "⛔️ سهمیه روزانه شما تمام شد ({n} دانلود). فردا دوباره بیایید!",
        "en": "⛔️ You've reached your daily limit ({n} downloads). Come back tomorrow!",
    },
    "banned_msg": {
        "fa": "⛔️ دسترسی شما به این ربات مسدود شده است.",
        "en": "⛔️ You are banned from using this bot.",
    },
})
