"""Settings from .env (the token and API keys live only in .env on the VPS, never in git)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))


@dataclass
class Config:
    bot_token: str
    api_id: int
    api_hash: str
    data_dir: str
    download_dir: str
    ffmpeg_dir: str
    cookies: str
    max_concurrent: int
    per_user_hour: int
    max_audio_min: int
    max_video_min: int
    max_bytes: int
    admin_id: int


def load(env_file=None):
    load_dotenv(env_file or os.path.join(HERE, ".env"))
    g = os.environ.get
    data = g("DATA_DIR") or os.path.join(HERE, "data")
    bin_dir = os.path.join(HERE, "bin")
    return Config(
        bot_token=g("BOT_TOKEN", ""),
        api_id=int(g("API_ID", "0") or 0),
        api_hash=g("API_HASH", ""),
        data_dir=data,
        download_dir=g("DOWNLOAD_DIR") or os.path.join(data, "downloads"),
        # a static ffmpeg in ./bin is used when there (install.sh puts it there if the VPS has none)
        ffmpeg_dir=g("FFMPEG_DIR") or (bin_dir if os.path.exists(os.path.join(bin_dir, "ffmpeg")) else ""),
        cookies=g("COOKIES_FILE") or os.path.join(data, "cookies.txt"),
        max_concurrent=int(g("MAX_CONCURRENT", "2")),
        per_user_hour=int(g("PER_USER_HOUR", "20")),
        max_audio_min=int(g("MAX_AUDIO_MIN", "120")),
        max_video_min=int(g("MAX_VIDEO_MIN", "60")),
        max_bytes=int(g("MAX_MB", "1900")) * 1024 * 1024,
        admin_id=int(g("ADMIN_ID", "0") or 0),
    )
