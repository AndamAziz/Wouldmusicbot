"""Would Music bot: search a song on YouTube and get it as MP3 (320 kbps) or video (up to 1080p).

Files are sent over MTProto (pyrofork), so up to 2 GB. Everyone can use it, with limits:
MAX_CONCURRENT downloads at once on the server, one job per user at a time, PER_USER_HOUR per hour.
A file that was sent once is remembered (Telegram file_id) and sent again instantly.
"""
import asyncio
import logging
import os
import shutil
import tempfile
import time

import yt_dlp
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import ytdl
from store import Store

log = logging.getLogger("wouldmusic")

HELP = ("🎶 **Would Music**\n\n"
        "ناوی گۆرانییەک یان هونەرمەندێک بنووسە، یان لینکی YouTube بنێرە.\n"
        "دوایی هەڵیبژێرە:\n"
        "🎵 **MP3** – دەنگ بە کوالیتی 320kbps\n"
        "🎬 **ڤیدیۆ** – 1080p / 720p / 480p / 360p\n\n"
        "لە گرووپ: `/song ناوی گۆرانی`")
KIND_LABEL = {"a320": "🎵 MP3 320kbps", **{f"v{h}": f"🎬 ڤیدیۆ {h}p" for h in ytdl.VIDEO_HEIGHTS}}


def results_keyboard(items):
    rows = [[InlineKeyboardButton(f"{i + 1}. {it.title[:48]} [{ytdl.fmt_duration(it.duration)}]", callback_data=f"p:{it.id}")]
            for i, it in enumerate(items)]
    return InlineKeyboardMarkup(rows)


def formats_keyboard(vid):
    v = [InlineKeyboardButton(KIND_LABEL[f"v{h}"].replace("ڤیدیۆ ", ""), callback_data=f"d:{vid}:v{h}") for h in ytdl.VIDEO_HEIGHTS]
    return InlineKeyboardMarkup([[InlineKeyboardButton(KIND_LABEL["a320"], callback_data=f"d:{vid}:a320")],
                                 v[:2], v[2:],
                                 [InlineKeyboardButton("▶️ YouTube", url=f"https://youtu.be/{vid}")]])


def friendly_error(e):
    s = str(e)
    if "Sign in to confirm" in s or "not a bot" in s:
        return "YouTube ڕێگری لە سێرڤەرەکە کرد. تکایە دواتر هەوڵ بدەرەوە (بەڕێوەبەر: cookies.txt دابنێ)."
    if "max_filesize" in s or "too big" in s or "File is larger" in s:
        return "فایلەکە زۆر گەورەیە. کوالیتییەکی نزمتر هەڵبژێرە."
    if "Private video" in s or "unavailable" in s.lower():
        return "ئەم ڤیدیۆیە بەردەست نییە."
    if "age" in s.lower() and "confirm" in s.lower():
        return "ئەم ڤیدیۆیە سنووری تەمەنی هەیە و دانابەزێت."
    return "هەڵەیەک ڕوویدا لە کاتی داگرتن. تکایە دووبارە هەوڵ بدەرەوە."


class Bot:
    def __init__(self, cfg):
        self.cfg = cfg
        os.makedirs(cfg.download_dir, exist_ok=True)
        self.store = Store(os.path.join(cfg.data_dir, "wouldmusic.db"))
        self.sem = asyncio.Semaphore(cfg.max_concurrent)
        self.active = set()
        self.app = Client("wouldmusic", api_id=cfg.api_id, api_hash=cfg.api_hash, bot_token=cfg.bot_token,
                          workdir=cfg.data_dir)
        a = self.app
        a.on_message(filters.command(["start", "help"]))(self.on_help)
        a.on_message(filters.command("stats"))(self.on_stats)
        a.on_message(filters.command(["song", "s"]))(self.on_song_cmd)
        a.on_message(filters.private & filters.text & ~filters.regex(r"^/"))(self.on_text)
        a.on_callback_query()(self.on_callback)

    # ---------------------------------------------------------------- helpers
    async def edit(self, msg, text, markup=None):
        try:
            await msg.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except MessageNotModified:
            pass
        except FloodWait as e:
            await asyncio.sleep(min(e.value, 30))
        except Exception:
            pass

    # ---------------------------------------------------------------- commands
    async def on_help(self, _, m: Message):
        if m.from_user:
            self.store.seen(m.from_user.id, m.from_user.first_name or "")
        await m.reply_text(HELP, disable_web_page_preview=True)

    async def on_stats(self, _, m: Message):
        if not self.cfg.admin_id or not m.from_user or m.from_user.id != self.cfg.admin_id:
            return
        s = self.store.stats()
        await m.reply_text(f"👥 بەکارهێنەر: {s['users']}\n📦 فایلی پاشەکەوتکراو: {s['cached']}\n"
                           f"⬇️ داگرتن لە 24 کاتژمێردا: {s['last24h']}\n⚙️ ئێستا لە کاردا: {len(self.active)}")

    async def on_song_cmd(self, _, m: Message):
        q = m.text.split(None, 1)[1] if len(m.text.split(None, 1)) > 1 else ""
        if not q:
            return await m.reply_text("بنووسە: `/song ناوی گۆرانی`")
        await self.handle_query(m, q)

    async def on_text(self, _, m: Message):
        await self.handle_query(m, m.text)

    async def handle_query(self, m: Message, q):
        if m.from_user:
            self.store.seen(m.from_user.id, m.from_user.first_name or "")
        q = q.strip()[:200]
        vid = ytdl.video_id(q)
        wait = await m.reply_text("🔎 ...")
        try:
            if vid:
                it = await asyncio.to_thread(ytdl.info, self.cfg, vid)
                return await self.edit(wait, f"🎶 **{it.title}**\n👤 {it.channel}  ⏱ {ytdl.fmt_duration(it.duration)}\n\nجۆرەکە هەڵبژێرە:",
                                       formats_keyboard(vid))
            items = await asyncio.to_thread(ytdl.search, self.cfg, q, 8)
        except Exception as e:
            log.warning("search failed: %s", e)
            return await self.edit(wait, friendly_error(e))
        if not items:
            return await self.edit(wait, "هیچ ئەنجامێک نەدۆزرایەوە. ناوێکی تر تاقی بکەرەوە.")
        await self.edit(wait, f"🔎 ئەنجامەکان بۆ: **{q}**\nیەکێکیان هەڵبژێرە:", results_keyboard(items))

    # ---------------------------------------------------------------- buttons
    async def on_callback(self, _, cq: CallbackQuery):
        data = cq.data or ""
        if data.startswith("p:"):
            vid = data[2:]
            try:
                it = await asyncio.to_thread(ytdl.info, self.cfg, vid)
            except Exception as e:
                return await cq.answer(friendly_error(e)[:190], show_alert=True)
            await cq.answer()
            return await self.edit(cq.message, f"🎶 **{it.title}**\n👤 {it.channel}  ⏱ {ytdl.fmt_duration(it.duration)}\n\nجۆرەکە هەڵبژێرە:",
                                   formats_keyboard(vid))
        if data.startswith("d:"):
            _, vid, kind = data.split(":", 2)
            if kind not in KIND_LABEL:
                return await cq.answer()
            return await self.deliver(cq, vid, kind)
        await cq.answer()

    async def deliver(self, cq: CallbackQuery, vid, kind):
        user = cq.from_user.id
        chat = cq.message.chat.id
        cached = self.store.cached(vid, kind)
        if cached:
            await cq.answer("📤")
            try:
                if kind == "a320":
                    await self.app.send_audio(chat, cached, caption="🎶 @Wouldmusicbot")
                else:
                    await self.app.send_video(chat, cached, caption="🎶 @Wouldmusicbot")
                return
            except Exception:
                self.store.forget(vid, kind)            # file_id no longer valid: download again
        if user in self.active:
            return await cq.answer("⏳ داگرتنێکی ترت لە کاردایە، چاوەڕێ بکە.", show_alert=True)
        if self.cfg.per_user_hour and self.store.used_last_hour(user) >= self.cfg.per_user_hour:
            return await cq.answer(f"سنووری {self.cfg.per_user_hour} داگرتن لە کاتژمێرێکدا تەواو بوو. دواتر هەوڵ بدەرەوە.",
                                   show_alert=True)
        await cq.answer()
        self.active.add(user)
        status = await self.app.send_message(chat, f"{KIND_LABEL[kind]}\n⏳ ئامادەکردن...")
        tmp = tempfile.mkdtemp(prefix=f"{vid}-", dir=self.cfg.download_dir)
        try:
            it = await asyncio.to_thread(ytdl.info, self.cfg, vid)
            limit = self.cfg.max_audio_min if kind == "a320" else self.cfg.max_video_min
            if it.duration and it.duration > limit * 60:
                return await self.edit(status, f"ئەم ڤیدیۆیە {ytdl.fmt_duration(it.duration)} ـە. "
                                               f"سنوور {limit} خولەکە بۆ {KIND_LABEL[kind]}.")
            if self.sem.locked():
                await self.edit(status, f"{KIND_LABEL[kind]}\n🕒 لە ڕیزدایت، کەمێک چاوەڕێ بکە...")
            async with self.sem:
                self.store.use(user)
                res = await self.download(status, vid, kind, tmp, it.title)
                await self.edit(status, f"📤 دەنێردرێت: **{res.title}**")
                sent = await self.upload(chat, status, res, kind)
            fid = (sent.audio or sent.video or sent.document).file_id if sent else None
            if fid:
                self.store.remember(vid, kind, fid)
            try:
                await status.delete()
            except Exception:
                pass
        except yt_dlp.utils.DownloadError as e:
            log.warning("download %s %s: %s", vid, kind, e)
            await self.edit(status, "❌ " + friendly_error(e))
        except Exception as e:
            log.exception("deliver %s %s", vid, kind)
            await self.edit(status, "❌ " + friendly_error(e))
        finally:
            self.active.discard(user)
            shutil.rmtree(tmp, ignore_errors=True)

    async def download(self, status, vid, kind, tmp, title):
        loop = asyncio.get_running_loop()
        last = [0.0]

        def hook(d):
            if d.get("status") != "downloading" or time.time() - last[0] < 4:
                return
            last[0] = time.time()
            done, total = d.get("downloaded_bytes") or 0, d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            pct = f"{done * 100 // total}%" if total else f"{done // 1048576} MB"
            asyncio.run_coroutine_threadsafe(self.edit(status, f"{KIND_LABEL[kind]}\n⬇️ **{title}**\n{pct}"), loop)

        await self.edit(status, f"{KIND_LABEL[kind]}\n⬇️ **{title}**")
        return await asyncio.to_thread(ytdl.download, self.cfg, vid, kind, tmp, hook)

    async def upload(self, chat, status, res, kind):
        last = [0.0]

        async def progress(cur, total):
            if time.time() - last[0] < 4 or not total:
                return
            last[0] = time.time()
            await self.edit(status, f"📤 **{res.title}**\n{cur * 100 // total}%")

        cap = f"🎶 **{res.performer} – {res.title}**\n@Wouldmusicbot" if res.performer else f"🎶 **{res.title}**\n@Wouldmusicbot"
        if kind == "a320":
            return await self.app.send_audio(chat, res.path, caption=cap, duration=res.duration, performer=res.performer,
                                             title=res.title, thumb=res.thumb, progress=progress,
                                             file_name=f"{(res.performer + ' - ') if res.performer else ''}{res.title}.mp3"[:120])
        return await self.app.send_video(chat, res.path, caption=cap, duration=res.duration, width=res.width,
                                         height=res.height, thumb=res.thumb, supports_streaming=True, progress=progress)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    cfg = config.load()
    missing = [k for k, v in (("BOT_TOKEN", cfg.bot_token), ("API_ID", cfg.api_id), ("API_HASH", cfg.api_hash)) if not v]
    if missing:
        raise SystemExit(f"missing in .env: {', '.join(missing)}")
    os.makedirs(cfg.data_dir, exist_ok=True)
    # leftovers of a previous run
    shutil.rmtree(cfg.download_dir, ignore_errors=True)
    Bot(cfg).app.run()


if __name__ == "__main__":
    main()
