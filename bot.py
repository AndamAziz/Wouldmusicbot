"""Would Music bot: search songs on YouTube, or send a link from YouTube, TikTok, Instagram, Facebook, X,
SoundCloud, Pinterest, Reddit, Twitch, Dailymotion ... and get it as MP3 (320 / 128 kbps) or video (best /
1080p / 720p / 480p / 360p).

Files go over MTProto (pyrofork), so up to 2 GB. Everyone can use it, with limits: MAX_CONCURRENT jobs at once
on the server, one job per user at a time, PER_USER_HOUR per hour. A file sent once is cached (file_id) and
sent again instantly. Per-user settings: ask every time / MP3 right away / video right away, and the qualities.
"""
import asyncio
import hashlib
import logging
import os
import secrets
import shutil
import tempfile
import threading
import time
from collections import OrderedDict

from pyrogram import Client, filters, idle
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import (BotCommand, CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup as K,
                            Message, ReplyKeyboardMarkup)

import config
import ytdl
from store import MODES, Store

log = logging.getLogger("wouldmusic")
BOT = "@Wouldmusicbot"
PAGE = 8

BTN_SEARCH, BTN_LINK, BTN_SETTINGS, BTN_HELP = "🔎 گەڕانی گۆرانی", "🔗 داگرتن لە لینک", "⚙️ ڕێکخستن", "❓ یارمەتی"
MENU = ReplyKeyboardMarkup([[BTN_SEARCH, BTN_LINK], [BTN_SETTINGS, BTN_HELP]], resize_keyboard=True,
                           is_persistent=True, placeholder="ناوی گۆرانی یان لینک بنووسە…")

KIND_LABEL = {"a320": "🎵 MP3 320", "a128": "🎵 MP3 128", "vbest": "🎬 باشترین کوالیتی",
              **{f"v{h}": f"🎬 {h}p" for h in ytdl.VIDEO_TIERS}}
MODE_LABEL = {"ask": "❔ هەموو جار بپرسە", "audio": "🎵 ڕاستەوخۆ MP3", "video": "🎬 ڕاستەوخۆ ڤیدیۆ"}

HELP = (f"🎶 **Would Music** – {BOT}\n\n"
        "**🔎 گەڕان:** ناوی گۆرانی یان هونەرمەند بنووسە.\n"
        "**🔗 لینک:** لینک لە YouTube، TikTok، Instagram، Facebook، X، SoundCloud، Pinterest، "
        "Reddit، Twitch، Dailymotion… بنێرە (تا 5 لینک پێکەوە).\n\n"
        "**🎵 MP3:** 320kbps یان 128kbps (بچووکتر)\n"
        "**🎬 ڤیدیۆ:** باشترین، 1080p، 720p، 480p، 360p – تا 2GB\n\n"
        "**⚡ خێرا:** لە ⚙️ ڕێکخستن «ڕاستەوخۆ MP3» یان «ڕاستەوخۆ ڤیدیۆ» هەڵبژێرە، "
        "ئیتر هەر لینکێک بنێریت یەکسەر دادەبەزێت بێ پرسیار.\n"
        "**♻️** هەر فایلێک جارێک نێردرابێت، جاری دووەم یەکسەر دێت.\n\n"
        "**لە گرووپ:** `/song ناو` – `/dl لینک`")


def bar(pct):
    pct = max(0, min(100, int(pct)))
    return "▰" * (pct // 10) + "▱" * (10 - pct // 10) + f" {pct}%"


def friendly_error(e):
    s = str(e)
    low = s.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        return "سەرچاوەکە ڕێگری لە سێرڤەرەکە کرد. دواتر هەوڵ بدەرەوە (بەڕێوەبەر: cookies.txt نوێ بکەرەوە)."
    if "login" in low or "logged-in" in low or "authentication" in low or "cookies" in low or "empty media response" in low:
        return "ئەم لینکە پێویستی بە چوونەژوورەوەیە (تایبەتە یان cookies نییە)."
    if "max_filesize" in low or "too big" in low or "larger than" in low:
        return "فایلەکە زۆر گەورەیە. کوالیتییەکی نزمتر هەڵبژێرە."
    if "unsupported url" in low:
        return "ئەم ماڵپەڕە پشتگیری ناکرێت."
    if "private" in low or "unavailable" in low or "not available" in low or "removed" in low or "no video" in low:
        return "ئەم پۆستە/ڤیدیۆیە بەردەست نییە."
    if "age" in low and "confirm" in low:
        return "سنووری تەمەنی هەیە و دانابەزێت."
    return "هەڵەیەک ڕوویدا. تکایە دووبارە هەوڵ بدەرەوە."


# ------------------------------------------------------------------ keyboards
def results_kb(qkey, items, keys, page):
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    rows = [[B(f"{i + 1}. {items[i].title[:45]} · {ytdl.fmt_duration(items[i].duration)}", callback_data=f"l:{keys[i]}")]
            for i in range(page * PAGE, min(len(items), (page + 1) * PAGE))]
    nav = []
    if page > 0:
        nav.append(B("⬅️", callback_data=f"s:{qkey}:{page - 1}"))
    nav.append(B(f"📄 {page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(B("➡️", callback_data=f"s:{qkey}:{page + 1}"))
    rows.append(nav)
    rows.append([B("✖️ داخستن", callback_data="x")])
    return K(rows)


def card_kb(key, media):
    rows = [[B(KIND_LABEL["a320"], callback_data=f"d:{key}:a320"), B(KIND_LABEL["a128"], callback_data=f"d:{key}:a128")]]
    if media.has_video:
        v = [B(KIND_LABEL[f"v{h}"], callback_data=f"d:{key}:v{h}") for h in media.qualities]
        if not v:
            v = [B(KIND_LABEL["vbest"], callback_data=f"d:{key}:vbest")]
        rows += [v[i:i + 2] for i in range(0, len(v), 2)]
    rows.append([B("🔗 سەرچاوە", url=media.url), B("✖️ داخستن", callback_data="x")])
    return K(rows)


def after_kb(key, kind, url, vq):
    """Under a sent file: the other format in one tap, and the source."""
    other = B("🎬 ڤیدیۆکەی", callback_data=f"d:{key}:{vq}") if kind in ytdl.AUDIO_KINDS \
        else B("🎵 MP3 ـەکەی", callback_data=f"d:{key}:a320")
    return K([[other, B("🔗 سەرچاوە", url=url)]])


def settings_kb(s):
    tick = lambda ok, t: ("✅ " if ok else "") + t
    return K([[B(tick(s["mode"] == m, MODE_LABEL[m]), callback_data=f"set:mode:{m}")] for m in MODES] +
             [[B(tick(s["aq"] == k, KIND_LABEL[k]), callback_data=f"set:aq:{k}") for k in ytdl.AUDIO_KINDS],
              [B(tick(s["vq"] == f"v{h}", f"{h}p"), callback_data=f"set:vq:v{h}") for h in ytdl.VIDEO_TIERS],
              [B("✖️ داخستن", callback_data="x")]])


SETTINGS_TEXT = ("⚙️ **ڕێکخستن**\n\n"
                 "**کاتێک لینک دەنێریت:** هەموو جار بپرسە، یان یەکسەر MP3 / ڤیدیۆ دابەزێنە (خێراتر).\n"
                 "**کوالیتی MP3** و **کوالیتی ڤیدیۆ** بۆ داگرتنی ڕاستەوخۆ و دوگمەی «ڤیدیۆکەی».")


def pick_video_kind(media, vq):
    """The user's video quality, or the closest one the source has (lower first)."""
    if not media.qualities:
        return "vbest"
    want = int(vq[1:])
    lower = [q for q in media.qualities if q <= want]
    return f"v{max(lower) if lower else min(media.qualities)}"


def card_text(m):
    t = f"{m.site}\n\n{'🎬' if m.has_video else '🎵'} **{m.title}**"
    info = " · ".join(x for x in (f"👤 {m.uploader}" if m.uploader else "",
                                   f"⏱ {ytdl.fmt_duration(m.duration)}" if m.duration else "") if x)
    if info:
        t += "\n" + info
    if m.count > 1:
        t += f"\n🖼 {m.count} دانە – یەکەمیان دادەبەزێت"
    return t + "\n\nجۆر و کوالیتی هەڵبژێرە:"


class Bot:
    def __init__(self, cfg):
        self.cfg = cfg
        os.makedirs(cfg.download_dir, exist_ok=True)
        self.store = Store(os.path.join(cfg.data_dir, "wouldmusic.db"))
        self.sem = asyncio.Semaphore(cfg.max_concurrent)
        self.active = {}                      # user -> job id
        self.jobs = {}                        # job id -> {"cancel": Event, "user": int, "uploading": bool}
        self.waiting = 0
        self.queries = OrderedDict()          # qkey -> (query, items, keys)
        self.meta = OrderedDict()             # link key -> ytdl.Media (so a click needs no second lookup)
        self.app = Client("wouldmusic", api_id=cfg.api_id, api_hash=cfg.api_hash, bot_token=cfg.bot_token,
                          workdir=cfg.data_dir)
        a = self.app
        a.on_message(filters.command(["start", "help"]))(self.on_help)
        a.on_message(filters.command("settings"))(self.on_settings)
        a.on_message(filters.command("stats"))(self.on_stats)
        a.on_message(filters.command(["song", "s"]))(self.on_song_cmd)
        a.on_message(filters.command(["dl", "d"]))(self.on_dl_cmd)
        a.on_message(filters.private & filters.text & ~filters.regex(r"^/"))(self.on_text)
        a.on_callback_query()(self.on_callback)

    # ---------------------------------------------------------------- helpers
    def remember_meta(self, key, media):
        self.meta[key] = media
        self.meta.move_to_end(key)
        while len(self.meta) > 2000:
            self.meta.popitem(last=False)

    async def edit(self, msg, text, markup=None):
        try:
            if msg.photo:
                await msg.edit_caption(text, reply_markup=markup)
            else:
                await msg.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except MessageNotModified:
            pass
        except FloodWait as e:
            await asyncio.sleep(min(e.value, 30))
        except Exception:
            pass

    def seen(self, m):
        if m.from_user:
            self.store.seen(m.from_user.id, m.from_user.first_name or "")

    # ---------------------------------------------------------------- commands
    async def on_help(self, _, m: Message):
        self.seen(m)
        await m.reply_text(HELP, reply_markup=MENU if m.chat.type.value == "private" else None,
                           disable_web_page_preview=True)

    async def on_settings(self, _, m: Message):
        if m.from_user:
            await m.reply_text(SETTINGS_TEXT, reply_markup=settings_kb(self.store.settings(m.from_user.id)))

    async def on_stats(self, _, m: Message):
        if not self.cfg.admin_id or not m.from_user or m.from_user.id != self.cfg.admin_id:
            return
        s = self.store.stats()
        await m.reply_text(f"📊 **ئامار**\n👥 بەکارهێنەر: {s['users']}\n📦 فایلی پاشەکەوتکراو: {s['cached']}\n"
                           f"⬇️ داگرتن لە 24 کاتژمێردا: {s['last24h']}\n⚙️ ئێستا لە کاردا: {len(self.jobs)}"
                           f"\n🕒 لە ڕیزدا: {self.waiting}")

    async def on_song_cmd(self, _, m: Message):
        parts = m.text.split(None, 1)
        if len(parts) < 2:
            return await m.reply_text("بنووسە: `/song ناوی گۆرانی`")
        await self.search(m, parts[1])

    async def on_dl_cmd(self, _, m: Message):
        urls = ytdl.urls_in(m.text)
        if not urls:
            return await m.reply_text("بنووسە: `/dl لینک`")
        for u in urls:
            await self.open_link(m, u)

    async def on_text(self, _, m: Message):
        self.seen(m)
        t = (m.text or "").strip()
        if t == BTN_SEARCH:
            return await m.reply_text("🔎 ناوی گۆرانی یان هونەرمەند بنووسە:", reply_markup=MENU)
        if t == BTN_LINK:
            return await m.reply_text("🔗 لینکەکە بنێرە (YouTube، TikTok، Instagram، Facebook، X، SoundCloud …):",
                                      reply_markup=MENU)
        if t == BTN_SETTINGS:
            return await self.on_settings(_, m)
        if t == BTN_HELP:
            return await self.on_help(_, m)
        urls = ytdl.urls_in(t)
        if urls:
            for u in urls:
                await self.open_link(m, u)
            return
        await self.search(m, t)

    # ---------------------------------------------------------------- search / link
    async def search(self, m: Message, q):
        q = q.strip()[:200]
        status = await m.reply_text(f"🔎 دەگەڕێم بۆ: **{q}** …")
        try:
            items = await asyncio.to_thread(ytdl.search, self.cfg, q, 16)
        except Exception as e:
            log.warning("search failed: %s", e)
            return await self.edit(status, "❌ " + friendly_error(e))
        if not items:
            return await self.edit(status, "هیچ ئەنجامێک نەدۆزرایەوە. ناوێکی تر تاقی بکەرەوە.")
        keys = []
        for it in items:
            k = self.store.link(it.url, it.title)
            self.remember_meta(k, ytdl.Media(it.url, it.title, it.channel, it.duration, ytdl.SITES["youtube"],
                                             f"https://i.ytimg.com/vi/{it.id}/hqdefault.jpg", [], True))
            keys.append(k)
        qkey = hashlib.sha1(f"{q}{time.time()}".encode()).hexdigest()[:10]
        self.queries[qkey] = (q, items, keys)
        while len(self.queries) > 300:
            self.queries.popitem(last=False)
        await self.edit(status, f"🎶 ئەنجامەکان بۆ: **{q}**\nیەکێکیان هەڵبژێرە 👇", results_kb(qkey, items, keys, 0))

    async def open_link(self, m: Message, url):
        user = m.from_user.id if m.from_user else 0
        status = await m.reply_text("🔎 لینکەکە دەپشکنرێت…")
        try:
            media = await asyncio.to_thread(ytdl.probe, self.cfg, url)
        except Exception as e:
            log.warning("probe %s: %s", url, str(e)[:200])
            return await self.edit(status, "❌ " + friendly_error(e))
        key = self.store.link(media.url, media.title)
        self.remember_meta(key, media)
        s = self.store.settings(user)
        if s["mode"] == "audio" or (s["mode"] == "video" and not media.has_video):
            await self.safe_delete(status)
            return await self.deliver(m.chat.id, user, key, s["aq"])
        if s["mode"] == "video":
            await self.safe_delete(status)
            return await self.deliver(m.chat.id, user, key, pick_video_kind(media, s["vq"]))
        await self.show_card(m.chat.id, key, media, status)

    async def show_card(self, chat, key, media, replace=None):
        if replace is not None:
            await self.safe_delete(replace)
        text, kb = card_text(media), card_kb(key, media)
        if media.thumb:
            try:
                return await self.app.send_photo(chat, media.thumb, caption=text, reply_markup=kb)
            except Exception:
                pass
        await self.app.send_message(chat, text, reply_markup=kb, disable_web_page_preview=True)

    async def safe_delete(self, msg):
        try:
            await msg.delete()
        except Exception:
            pass

    # ---------------------------------------------------------------- buttons
    async def on_callback(self, _, cq: CallbackQuery):
        data = cq.data or ""
        user = cq.from_user.id
        try:
            if data == "noop":
                return await cq.answer()
            if data == "x":
                await cq.answer()
                return await self.safe_delete(cq.message)
            if data.startswith("s:"):
                _, qkey, page = data.split(":")
                if qkey not in self.queries:
                    return await cq.answer("ئەم گەڕانە کۆن بووە، دووبارە بنووسە.", show_alert=True)
                q, items, keys = self.queries[qkey]
                await cq.answer()
                return await self.edit(cq.message, f"🎶 ئەنجامەکان بۆ: **{q}**\nیەکێکیان هەڵبژێرە 👇",
                                       results_kb(qkey, items, keys, int(page)))
            if data.startswith("l:"):
                key = data[2:]
                url, _t = self.store.url(key)
                if not url:
                    return await cq.answer("ئەم لینکە کۆن بووە.", show_alert=True)
                await cq.answer("⏳")
                s = self.store.settings(user)
                media = self.meta.get(key)
                if s["mode"] == "audio":
                    return await self.deliver(cq.message.chat.id, user, key, s["aq"])
                if media is None or (media.has_video and not media.qualities and "youtube" in url):
                    old = media
                    media = await asyncio.to_thread(ytdl.probe, self.cfg, url)
                    if old is not None and not media.thumb:
                        media.thumb = old.thumb
                    self.remember_meta(key, media)
                if s["mode"] == "video" and media.has_video:
                    return await self.deliver(cq.message.chat.id, user, key, pick_video_kind(media, s["vq"]))
                return await self.show_card(cq.message.chat.id, key, media)
            if data.startswith("d:"):
                _, key, kind = data.split(":", 2)
                if kind not in KIND_LABEL:
                    return await cq.answer()
                return await self.deliver(cq.message.chat.id, user, key, kind, cq)
            if data.startswith("c:"):
                job = self.jobs.get(data[2:])
                if not job or job["user"] != user:
                    return await cq.answer("ئەم داگرتنە تەواو بووە.")
                job["cancel"].set()
                return await cq.answer("❌ هەڵوەشێنرایەوە")
            if data.startswith("set:"):
                _, field, value = data.split(":", 2)
                ok = (field == "mode" and value in MODES) or (field == "aq" and value in ytdl.AUDIO_KINDS) or \
                     (field == "vq" and value in ytdl.VIDEO_KINDS)
                if not ok:
                    return await cq.answer()
                s = self.store.set(user, field, value)
                await cq.answer("✅ پاشەکەوت کرا")
                return await self.edit(cq.message, SETTINGS_TEXT, settings_kb(s))
            await cq.answer()
        except Exception as e:
            log.warning("callback %s: %s", data, str(e)[:200])
            try:
                await cq.answer("❌ " + friendly_error(e)[:180], show_alert=True)
            except Exception:
                pass

    # ---------------------------------------------------------------- download + send
    async def deliver(self, chat, user, key, kind, cq: CallbackQuery | None = None):
        answer = (lambda text=None, alert=False: cq.answer(text, show_alert=alert)) if cq else \
                 (lambda text=None, alert=False: self.app.send_message(chat, text) if text else asyncio.sleep(0))
        url, title = self.store.url(key)
        if not url:
            return await answer("ئەم لینکە کۆن بووە، دووبارە بینێرە.", True)
        vq = self.store.settings(user)["vq"]
        cached = self.store.cached(key, kind)
        if cached:
            if cq:
                await cq.answer("⚡")
            try:
                send = self.app.send_audio if kind in ytdl.AUDIO_KINDS else self.app.send_video
                await send(chat, cached, caption=f"♻️ {title or ''}\n🤖 {BOT}", reply_markup=after_kb(key, kind, url, vq))
                log.info("sent from cache %s %s (user %s)", key, kind, user)
                return
            except Exception:
                self.store.forget(key, kind)              # file_id no longer valid: download again
        if user in self.active:
            return await answer("⏳ داگرتنێکی ترت لە کاردایە. چاوەڕێ بکە یان ❌ هەڵیوەشێنەرەوە.", True)
        if self.cfg.per_user_hour and self.store.used_last_hour(user) >= self.cfg.per_user_hour:
            return await answer(f"سنووری {self.cfg.per_user_hour} داگرتن لە کاتژمێرێکدا تەواو بوو. دواتر هەوڵ بدەرەوە.", True)
        if cq:
            await cq.answer()
        job_id = secrets.token_hex(4)
        job = {"cancel": threading.Event(), "user": user, "uploading": False}
        self.jobs[job_id] = job
        self.active[user] = job_id
        cancel_kb = K([[B("❌ هەڵوەشاندنەوە", callback_data=f"c:{job_id}")]])
        label = KIND_LABEL[kind]
        status = await self.app.send_message(chat, f"{label}\n⏳ ئامادەکردن…", reply_markup=cancel_kb)
        tmp = tempfile.mkdtemp(prefix="job-", dir=self.cfg.download_dir)
        try:
            media = self.meta.get(key)
            if media is None or not media.duration:
                media = await asyncio.to_thread(ytdl.probe, self.cfg, url)
                self.remember_meta(key, media)
            limit = self.cfg.max_audio_min if kind in ytdl.AUDIO_KINDS else self.cfg.max_video_min
            if media.duration and media.duration > limit * 60:
                return await self.edit(status, f"⛔️ ئەمە {ytdl.fmt_duration(media.duration)} ـە. "
                                               f"سنوور {limit} خولەکە بۆ {label}.")
            queued = self.sem.locked()
            if queued:
                self.waiting += 1
                await self.edit(status, f"{label}\n🕒 لە ڕیزدایت ({self.waiting})… کەمێک چاوەڕێ بکە", cancel_kb)
            try:
                await self.sem.acquire()
            finally:
                if queued:
                    self.waiting -= 1
            try:
                if job["cancel"].is_set():
                    raise ytdl.Cancelled()
                self.store.use(user)
                t0 = time.time()
                res = await self.download(status, url, kind, tmp, media.title, job, cancel_kb)
                size = os.path.getsize(res.path)
                log.info("downloaded %s %s %s in %ds (user %s)", key, kind, ytdl.fmt_size(size), time.time() - t0, user)
                job["uploading"] = True
                t1 = time.time()
                sent = await self.upload(chat, status, res, kind, media, size, key, url, vq, cancel_kb, job)
                if job["cancel"].is_set() or sent is None:
                    raise ytdl.Cancelled()
                log.info("sent %s %s %s in %ds: %s", key, kind, ytdl.fmt_size(size), time.time() - t1, res.title)
            finally:
                self.sem.release()
            fid = (sent.audio or sent.video or sent.document)
            if fid:
                self.store.remember(key, kind, fid.file_id)
            await self.safe_delete(status)
        except ytdl.Cancelled:
            await self.edit(status, f"{label}\n❌ هەڵوەشێنرایەوە.")
        except Exception as e:
            log.warning("deliver %s %s: %s", url, kind, str(e)[:300])
            await self.edit(status, f"{label}\n❌ " + friendly_error(e))
        finally:
            self.jobs.pop(job_id, None)
            if self.active.get(user) == job_id:
                self.active.pop(user, None)
            shutil.rmtree(tmp, ignore_errors=True)

    async def download(self, status, url, kind, tmp, title, job, cancel_kb):
        loop = asyncio.get_running_loop()
        last = [0.0]

        def hook(d):
            if d.get("status") != "downloading" or time.time() - last[0] < 3:
                return
            last[0] = time.time()
            done, total = d.get("downloaded_bytes") or 0, d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed, eta = d.get("speed") or 0, d.get("eta")
            line = bar(done * 100 / total) if total else f"⬇️ {ytdl.fmt_size(done)}"
            extra = " · ".join(x for x in (f"⚡ {ytdl.fmt_size(speed)}/s" if speed else "",
                                           f"⏳ {ytdl.fmt_duration(eta)}" if eta else "") if x)
            asyncio.run_coroutine_threadsafe(
                self.edit(status, f"{KIND_LABEL[kind]}\n⬇️ **{title[:80]}**\n{line}\n{extra}", cancel_kb), loop)

        await self.edit(status, f"{KIND_LABEL[kind]}\n⬇️ **{title[:80]}**\n{bar(0)}", cancel_kb)
        return await asyncio.to_thread(ytdl.download, self.cfg, url, kind, tmp, hook, job["cancel"])

    async def upload(self, chat, status, res, kind, media, size, key, url, vq, cancel_kb, job):
        last = [0.0]

        async def progress(cur, total):
            if job["cancel"].is_set():
                self.app.stop_transmission()          # only allowed here: the send returns None
            if time.time() - last[0] < 3 or not total:
                return
            last[0] = time.time()
            await self.edit(status, f"📤 **{res.title[:80]}**\n{bar(cur * 100 / total)}", cancel_kb)

        name = f"{res.performer} – {res.title}" if res.performer else res.title
        cap = (f"{'🎵' if kind in ytdl.AUDIO_KINDS else '🎬'} **{name[:200]}**\n"
               f"⏱ {ytdl.fmt_duration(res.duration)} · 💾 {ytdl.fmt_size(size)} · {media.site}\n🤖 {BOT}")
        kb = after_kb(key, kind, url, vq)
        await self.edit(status, f"📤 **{res.title[:80]}**\n{bar(0)}", cancel_kb)
        if kind in ytdl.AUDIO_KINDS:
            return await self.app.send_audio(chat, res.path, caption=cap, duration=res.duration, performer=res.performer,
                                             title=res.title, thumb=res.thumb, progress=progress, reply_markup=kb,
                                             file_name=f"{name}.mp3"[:120])
        return await self.app.send_video(chat, res.path, caption=cap, duration=res.duration, width=res.width,
                                         height=res.height, thumb=res.thumb, supports_streaming=True,
                                         progress=progress, reply_markup=kb)


async def run(cfg):
    bot = Bot(cfg)
    await bot.app.start()
    try:
        await bot.app.set_bot_commands([
            BotCommand("start", "دەستپێکردن و مێنیو"), BotCommand("help", "یارمەتی"),
            BotCommand("settings", "ڕێکخستن (خێرا: ڕاستەوخۆ MP3 / ڤیدیۆ)"),
            BotCommand("song", "گەڕانی گۆرانی: /song ناو"), BotCommand("dl", "داگرتن لە لینک: /dl لینک")])
    except Exception as e:
        log.warning("set_bot_commands: %s", e)
    me = await bot.app.get_me()
    log.info("started as @%s", me.username)
    await idle()
    await bot.app.stop()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    # Telegram slows big uploads a little ("Waiting for 1 seconds ... SaveBigFilePart"): normal, not an error
    logging.getLogger("pyrogram.session.session").setLevel(logging.ERROR)
    cfg = config.load()
    missing = [k for k, v in (("BOT_TOKEN", cfg.bot_token), ("API_ID", cfg.api_id), ("API_HASH", cfg.api_hash)) if not v]
    if missing:
        raise SystemExit(f"missing in .env: {', '.join(missing)}")
    os.makedirs(cfg.data_dir, exist_ok=True)
    shutil.rmtree(cfg.download_dir, ignore_errors=True)       # leftovers of a previous run
    asyncio.run(run(cfg))


if __name__ == "__main__":
    main()
