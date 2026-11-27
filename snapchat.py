"""Snapchat: Spotlight videos, share links (snapchat.com/t/...), a public profile's current Story and its
Highlights. yt-dlp only knows Spotlight (and refuses Snapchat's media links), so the public page is read here:
the JSON-LD VideoObject (one video) and the __NEXT_DATA__ JSON (story / highlights / spotlight lists).
Files are downloaded straight from Snapchat's CDN."""
import json
import os
import re
import subprocess
import shutil
import threading
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field

import ytdl

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
PAGE_RE = re.compile(r"^https?://([a-z0-9-]+\.)*snapchat\.com/", re.I)
CDN_RE = re.compile(r"^https?://[a-z0-9.-]*sc-cdn\.net/", re.I)
SITE = "👻 Snapchat"
MAX_ITEMS = 30          # snaps per album download


@dataclass
class Snap:
    url: str            # direct media link (CDN)
    kind: str           # "video" / "image"
    thumb: str = ""
    title: str = ""


@dataclass
class SnapPage:
    url: str
    user: str = ""
    title: str = ""
    picture: str = ""
    single: Snap | None = None                              # a Spotlight / one shared snap
    groups: "OrderedDict[str, list]" = field(default_factory=OrderedDict)   # name -> [Snap]


def is_page(url):
    return bool(PAGE_RE.match(url or ""))


def is_cdn(url):
    return bool(CDN_RE.match(url or ""))


def fetch(url, timeout=30):
    """(final url after redirects, html)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl(), r.read(4_000_000).decode("utf-8", "replace")


def _snap(item):
    u = (item.get("snapUrls") or {})
    media = u.get("mediaUrl")
    if not media:
        return None
    prev = u.get("mediaPreviewUrl") or {}
    title = item.get("snapTitle") or ""
    return Snap(media, "video" if item.get("snapMediaType") == 1 else "image",
                prev.get("value") if isinstance(prev, dict) else str(prev or ""), title if isinstance(title, str) else "")


def parse(url, html):
    """SnapPage from a Snapchat page."""
    page = SnapPage(url)
    for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            d = json.loads(block)
        except ValueError:
            continue
        for x in d if isinstance(d, list) else [d]:
            if isinstance(x, dict) and x.get("@type") == "VideoObject" and x.get("contentUrl"):
                page.single = Snap(x["contentUrl"], "video", x.get("thumbnailUrl") or "",
                                   (x.get("name") or x.get("description") or "").strip())
                page.title = page.single.title
            elif isinstance(x, dict) and x.get("@type") == "BreadcrumbList":
                for it in x.get("itemListElement") or []:
                    m = re.search(r"\(@([\w.\-]+)\)", (it.get("name") or ""))
                    if m:
                        page.user = m.group(1)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            pp = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
        except ValueError:
            pp = {}
        prof = (pp.get("userProfile") or {}).get("publicProfileInfo") or (pp.get("userProfile") or {}).get("userInfo") or {}
        page.user = page.user or prof.get("username") or ""
        page.title = page.title or prof.get("title") or prof.get("displayName") or page.user
        page.picture = prof.get("profilePictureUrl") or ""
        story = [s for s in (_snap(i) for i in ((pp.get("story") or {}).get("snapList") or [])) if s]
        if story:
            page.groups["📖 ستۆری"] = story
        for h in pp.get("curatedHighlights") or []:
            snaps = [s for s in (_snap(i) for i in h.get("snapList") or []) if s]
            if snaps:
                name = ((h.get("storyTitle") or {}).get("value") or "Highlight").strip()
                key = f"⭐ {name}"
                while key in page.groups:
                    key += " "
                page.groups[key] = snaps
        spot = [s for h in pp.get("spotlightHighlights") or [] for s in (_snap(i) for i in h.get("snapList") or []) if s]
        if spot:
            page.groups["🎬 Spotlight"] = spot
    return page


def load(url):
    final, html = fetch(url)
    page = parse(final, html)
    if not page.single and not page.groups:
        raise ytdl.yt_dlp.utils.DownloadError("snapchat: no public video / story on this page (private or expired)")
    return page


def download_file(url, path_base, progress=None, cancel: threading.Event | None = None, max_bytes=2_000_000_000):
    """Download a CDN file; returns the path (extension from the content type). Raises ytdl.Cancelled."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        ext = {"video/mp4": "mp4", "video/quicktime": "mov", "image/jpeg": "jpg", "image/png": "png",
               "image/webp": "webp"}.get(ctype, "mp4" if "video" in ctype else "jpg")
        total = int(r.headers.get("Content-Length") or 0)
        if total and total > max_bytes:
            raise ytdl.yt_dlp.utils.DownloadError("File is larger than max_filesize")
        path, done = f"{path_base}.{ext}", 0
        with open(path, "wb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    raise ytdl.Cancelled()
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if done > max_bytes:
                    raise ytdl.yt_dlp.utils.DownloadError("File is larger than max_filesize")
                if progress:
                    progress({"status": "downloading", "downloaded_bytes": done, "total_bytes": total})
    return path


def _ff(cfg, name):
    return os.path.join(cfg.ffmpeg_dir, name) if cfg.ffmpeg_dir else (shutil.which(name) or name)


def video_info(cfg, path):
    """(duration s, width, height) with ffprobe; zeros when unknown."""
    try:
        out = subprocess.run([_ff(cfg, "ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height:format=duration", "-of", "json", path],
                             capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL).stdout
        d = json.loads(out or "{}")
        s = (d.get("streams") or [{}])[0]
        return int(float((d.get("format") or {}).get("duration") or 0)), int(s.get("width") or 0), int(s.get("height") or 0)
    except Exception:
        return 0, 0, 0


def fetch_result(cfg, url, kind, workdir, progress=None, cancel=None, title="Snapchat"):
    """Download one Snapchat media link as kind (vbest = the video as it is, a320 / a128 = MP3) -> ytdl.Result."""
    os.makedirs(workdir, exist_ok=True)
    src = download_file(url, os.path.join(workdir, "media"), progress, cancel, cfg.max_bytes)
    thumb = ytdl._thumb(cfg, src, os.path.join(workdir, "thumb.jpg")) if src.endswith(("jpg", "png", "webp")) else None
    if src.endswith(("mp4", "mov")) and not thumb:
        frame = os.path.join(workdir, "frame.jpg")
        subprocess.run([_ff(cfg, "ffmpeg"), "-y", "-loglevel", "error", "-ss", "0.5", "-i", src, "-frames:v", "1", frame],
                       timeout=60, stdin=subprocess.DEVNULL)
        thumb = ytdl._thumb(cfg, frame, os.path.join(workdir, "thumb.jpg"))
    dur, w, h = video_info(cfg, src)
    path = src
    if kind in ytdl.AUDIO_KINDS:
        path = os.path.join(workdir, "audio.mp3")
        r = subprocess.run([_ff(cfg, "ffmpeg"), "-y", "-loglevel", "error", "-i", src, "-vn", "-b:a", f"{kind[1:]}k", path],
                           timeout=600, stdin=subprocess.DEVNULL)
        if r.returncode or not os.path.exists(path):
            raise ytdl.yt_dlp.utils.DownloadError("this snap has no sound")
    return ytdl.Result(path, title or "Snapchat", "", dur, thumb, w, h)
