"""Search and download with yt-dlp (no Telegram code here, so it can be tested on its own)."""
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

import yt_dlp

YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")

# Video qualities offered (height). The best stream up to that height is used, H.264 + AAC first,
# so Telegram can play it inside the app.
VIDEO_HEIGHTS = (1080, 720, 480, 360)


@dataclass
class Item:
    id: str
    title: str
    duration: int = 0
    channel: str = ""


@dataclass
class Result:
    path: str
    title: str
    performer: str
    duration: int
    thumb: str | None = None
    width: int = 0
    height: int = 0
    extra: list = field(default_factory=list)   # other temp files to delete


def fmt_duration(sec):
    sec = int(sec or 0)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def video_id(text):
    """YouTube video id in a link, or None."""
    m = YT_ID.search(text or "")
    return m.group(1) if m else None


def _base_opts(cfg):
    o = {"quiet": True, "no_warnings": True, "no_color": True, "noplaylist": True, "socket_timeout": 30,
         "retries": 3, "fragment_retries": 3, "concurrent_fragment_downloads": 4}
    if cfg.cookies and os.path.exists(cfg.cookies):
        o["cookiefile"] = cfg.cookies
    if cfg.ffmpeg_dir:
        o["ffmpeg_location"] = cfg.ffmpeg_dir
    return o


def search(cfg, query, limit=8):
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


def info(cfg, vid):
    """Item for one video id (title, duration, channel)."""
    with yt_dlp.YoutubeDL(_base_opts(cfg) | {"skip_download": True}) as y:
        e = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
    return Item(vid, e.get("title") or vid, int(e.get("duration") or 0), e.get("channel") or e.get("uploader") or "")


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


def download(cfg, vid, kind, workdir, progress=None):
    """Download video `vid` as kind 'a320' (MP3 320 kbps) or 'v1080' / 'v720' / ... (MP4).
    Returns Result. Raises yt_dlp.utils.DownloadError when it fails."""
    os.makedirs(workdir, exist_ok=True)
    out = os.path.join(workdir, f"{vid}.%(ext)s")
    opts = _base_opts(cfg) | {"outtmpl": out, "writethumbnail": True, "max_filesize": cfg.max_bytes}
    if progress:
        opts["progress_hooks"] = [progress]
    if kind == "a320":
        opts |= {"format": "bestaudio/best",
                 "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"},
                                    {"key": "FFmpegMetadata"}]}
        ext = "mp3"
    else:
        h = int(kind[1:])
        opts |= {"format": (f"bestvideo[height<={h}][vcodec^=avc1]+bestaudio[ext=m4a]/"
                            f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"),
                 "merge_output_format": "mp4",
                 "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]}
        ext = "mp4"
    with yt_dlp.YoutubeDL(opts) as y:
        e = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=True)
    path = os.path.join(workdir, f"{vid}.{ext}")
    if not os.path.exists(path):
        raise yt_dlp.utils.DownloadError("file was not created (too big or not available)")
    thumbs = [os.path.join(workdir, f) for f in os.listdir(workdir)
              if f.startswith(vid + ".") and f.rsplit(".", 1)[-1].lower() in ("jpg", "jpeg", "webp", "png")]
    thumb = _thumb(cfg, thumbs[0] if thumbs else None, os.path.join(workdir, f"{vid}.thumb.jpg"))
    performer, title = _split_title(e.get("title") or vid, e.get("channel") or e.get("uploader") or "")
    return Result(path, title, performer, int(e.get("duration") or 0), thumb,
                  int(e.get("width") or 0), int(e.get("height") or 0), thumbs)
