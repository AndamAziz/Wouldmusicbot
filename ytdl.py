"""Search and download with yt-dlp - YouTube and every site yt-dlp knows (TikTok, Instagram, Facebook,
X, SoundCloud, Pinterest, Reddit, Twitch, Dailymotion, Vimeo ...). No Telegram code here."""
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field

import yt_dlp

YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")
URL_RE = re.compile(r"https?://[^\s<>\"']+")

# Qualities offered for video (the SHORT side, so a vertical TikTok / Reel 720x1280 counts as 720p).
VIDEO_TIERS = (1080, 720, 480, 360)
AUDIO_KINDS = ("a320", "a128")
VIDEO_KINDS = ("vbest",) + tuple(f"v{h}" for h in VIDEO_TIERS)

SITES = {
    "youtube": "▶️ YouTube", "tiktok": "🎵 TikTok", "instagram": "📸 Instagram", "facebook": "📘 Facebook",
    "twitter": "🐦 X", "soundcloud": "☁️ SoundCloud", "pinterest": "📌 Pinterest", "reddit": "👽 Reddit",
    "vimeo": "🎞 Vimeo", "dailymotion": "🎞 Dailymotion", "twitch": "🟣 Twitch", "snapchat": "👻 Snapchat",
    "threads": "🧵 Threads", "likee": "❤️ Likee", "bilibili": "📺 Bilibili", "kick": "🟢 Kick",
}


class Cancelled(Exception):
    pass


@dataclass
class Item:
    id: str
    title: str
    duration: int = 0
    channel: str = ""

    @property
    def url(self):
        return f"https://www.youtube.com/watch?v={self.id}"


@dataclass
class Media:
    url: str
    title: str
    uploader: str = ""
    duration: int = 0
    site: str = "🌐"
    thumb: str = ""
    qualities: list = field(default_factory=list)   # video tiers the source really has
    has_video: bool = True
    count: int = 1                                   # items in a carousel / album


@dataclass
class Result:
    path: str
    title: str
    performer: str
    duration: int
    thumb: str | None = None
    width: int = 0
    height: int = 0


def fmt_duration(sec):
    sec = int(sec or 0)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_size(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u in ("B", "KB") else f"{n:.1f} {u}"
        n /= 1024


def video_id(text):
    """YouTube video id in a link, or None."""
    m = YT_ID.search(text or "")
    return m.group(1) if m else None


def urls_in(text, limit=5):
    """Links in a message (in order, no repeats, at most `limit`)."""
    out = []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(").,;!?»”")
        if u not in out:
            out.append(u)
    return out[:limit]


def site_label(extractor_key):
    k = (extractor_key or "").lower()
    return next((v for s, v in SITES.items() if k.startswith(s)), "🌐 " + (extractor_key or "Web"))


def _base_opts(cfg):
    o = {"quiet": True, "no_warnings": True, "no_color": True, "noprogress": True, "noplaylist": True, "socket_timeout": 30,
         "retries": 3, "fragment_retries": 3, "concurrent_fragment_downloads": 4}
    if cfg.cookies and os.path.exists(cfg.cookies):
        o["cookiefile"] = cfg.cookies
    if cfg.ffmpeg_dir:
        o["ffmpeg_location"] = cfg.ffmpeg_dir
    return o


def search(cfg, query, limit=16):
    """[Item] for a text search on YouTube."""
    opts = _base_opts(cfg) | {"extract_flat": "in_playlist", "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(f"ytsearch{limit}:{query}", download=False)
    out = []
    for e in (info or {}).get("entries") or []:
        if not e or not e.get("id") or len(e["id"]) != 11:
            continue
        if e.get("live_status") in ("is_live", "is_upcoming"):
            continue
        out.append(Item(e["id"], (e.get("title") or "").strip(), int(e.get("duration") or 0),
                        e.get("channel") or e.get("uploader") or ""))
    return out


def qualities(formats):
    """Video tiers available, from the short side of every video format; [] when unknown."""
    short = [min(f["width"], f["height"]) for f in formats or []
             if f.get("width") and f.get("height") and f.get("vcodec") not in (None, "none")]
    if not short:
        return []
    top = max(short)
    return [t for t in VIDEO_TIERS if top >= t * 0.9]


def _media(url, e):
    if e.get("_type") == "playlist":                 # carousel / album: the first item, with the count
        entries = [x for x in e.get("entries") or [] if x]
        if not entries:
            return Media(url, e.get("title") or url, has_video=False)
        m = _media(url, entries[0])
        m.count = len(entries)
        return m
    fmts = e.get("formats") or []
    # audio-only sources (SoundCloud ...) mark every format vcodec "none"; unknown codecs count as video
    has_video = not all(f.get("vcodec") == "none" for f in fmts) if fmts else e.get("vcodec") != "none"
    text = (e.get("title") or e.get("description") or "").strip()
    title = text.splitlines()[0][:150] if text else url
    return Media(url=e.get("webpage_url") or url, title=title, uploader=e.get("uploader") or e.get("channel") or "",
                 duration=int(e.get("duration") or 0), site=site_label(e.get("extractor_key")),
                 thumb=e.get("thumbnail") or "", qualities=qualities(fmts) if has_video else [], has_video=has_video)


def probe(cfg, url):
    """Media info of a link (no download). Raises yt_dlp.utils.DownloadError."""
    with yt_dlp.YoutubeDL(_base_opts(cfg) | {"skip_download": True}) as y:
        e = y.extract_info(url, download=False)
    return _media(url, e)


def _thumb(cfg, src, dst):
    """A Telegram thumbnail (JPEG, 320 px, < 200 KB) from any image, or None."""
    if not src or not os.path.exists(src):
        return None
    ff = os.path.join(cfg.ffmpeg_dir, "ffmpeg") if cfg.ffmpeg_dir else (shutil.which("ffmpeg") or "ffmpeg")
    try:
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", src, "-vf", "scale=320:320:force_original_aspect_ratio=decrease",
                        "-q:v", "5", dst], check=True, timeout=60, stdin=subprocess.DEVNULL)
        return dst if os.path.getsize(dst) < 200_000 else None
    except Exception:
        return None


def _split_title(title, channel):
    """'Artist - Song' -> (performer, title); otherwise (channel, title)."""
    t = re.sub(r"\s*[\(\[](official|lyric|lyrics|audio|video|music video|hd|4k)[^\)\]]*[\)\]]", "", title, flags=re.I).strip()
    if " - " in t:
        a, b = t.split(" - ", 1)
        return a.strip(), b.strip()
    return re.sub(r"\s*-\s*Topic$", "", channel or "").strip(), t


def format_opts(kind):
    """yt-dlp options for a kind: a320 / a128 (MP3) or vbest / v1080 / v720 ... (MP4, H.264 first so it plays in
    Telegram; the quality is the short side, so vertical videos get the right one)."""
    if kind in AUDIO_KINDS:
        return {"format": "bestaudio/best",
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": kind[1:]},
                                   {"key": "FFmpegMetadata"}]}, "mp3"
    h = 1080 if kind == "vbest" else int(kind[1:])
    return {"format": "bv*+ba/b", "format_sort": [f"res:{h}", "vcodec:h264", "acodec:aac", "ext:mp4:m4a"],
            "merge_output_format": "mp4",
            "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]}, "mp4"


def download(cfg, url, kind, workdir, progress=None, cancel: threading.Event | None = None):
    """Download `url` as `kind` into `workdir` (a fresh folder for this job). Returns Result.
    Raises Cancelled when `cancel` is set, yt_dlp.utils.DownloadError when it fails."""
    os.makedirs(workdir, exist_ok=True)

    def hook(d):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if progress:
            progress(d)

    extra, ext = format_opts(kind)
    opts = _base_opts(cfg) | {"outtmpl": os.path.join(workdir, "media.%(ext)s"), "writethumbnail": True,
                              "max_filesize": cfg.max_bytes, "progress_hooks": [hook]} | extra
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            e = y.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as err:
        if cancel is not None and cancel.is_set():
            raise Cancelled() from err
        raise
    if e.get("_type") == "playlist" and e.get("entries"):
        e = next((x for x in e["entries"] if x), e)
    files = [f for f in os.listdir(workdir) if f.startswith("media.")]
    media = [f for f in files if f.endswith("." + ext)] or \
            [f for f in files if f.rsplit(".", 1)[-1].lower() in ("mp4", "mkv", "webm", "mov", "m4a", "mp3", "opus", "ogg")]
    if not media:
        raise yt_dlp.utils.DownloadError("file was not created (too big or not available)")
    path = os.path.join(workdir, media[0])
    thumbs = [os.path.join(workdir, f) for f in files if f.rsplit(".", 1)[-1].lower() in ("jpg", "jpeg", "webp", "png", "image")]
    thumb = _thumb(cfg, thumbs[0] if thumbs else None, os.path.join(workdir, "thumb.jpg"))
    title = (e.get("title") or e.get("description") or "media").strip().splitlines()[0][:150]
    performer, title = _split_title(title, e.get("artist") or e.get("channel") or e.get("uploader") or "")
    return Result(path, title, performer, int(e.get("duration") or 0), thumb,
                  int(e.get("width") or 0), int(e.get("height") or 0))
