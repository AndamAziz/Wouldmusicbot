#!/usr/bin/env bash
# Would Music bot - install / repair on the VPS. Run as root from /opt/wouldmusic-bot:
#   sudo bash deploy/install.sh               (asks the Telegram settings only when missing / wrong)
#   sudo bash deploy/install.sh --reset-env   (asks them again)
# Everything stays inside /opt/wouldmusic-bot (own user "wouldmusic", own venv, own ffmpeg/deno in ./bin,
# own systemd service). Nothing else on the VPS (nginx, PHP, other bots, IPTV) is touched.
set -euo pipefail
DIR=/opt/wouldmusic-bot
USR=wouldmusic
cd "$DIR"
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Python 3.10+ is needed (python3 --version)"; exit 1; }
python3 -m venv --help >/dev/null 2>&1 || { echo "install python3-venv first: apt install python3-venv"; exit 1; }

id "$USR" >/dev/null 2>&1 || useradd --system --home-dir "$DIR" --shell /usr/sbin/nologin "$USR"
mkdir -p "$DIR/data" "$DIR/bin"

# Python packages in the bot's own venv
[ -x venv/bin/python ] || python3 -m venv venv
venv/bin/pip install -q -U pip
venv/bin/pip install -q -r requirements.txt

ARCH=$(uname -m)
# ffmpeg: the VPS one if there is one, else a static build in ./bin (no apt, nothing system-wide)
if ! command -v ffmpeg >/dev/null 2>&1 && [ ! -x bin/ffmpeg ]; then
  case "$ARCH" in x86_64) F=linux64;; aarch64) F=linuxarm64;; *) echo "unknown arch $ARCH"; exit 1;; esac
  echo "downloading ffmpeg ($F) into $DIR/bin"
  T=$(mktemp -d)
  curl -fsSL "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-$F-gpl.tar.xz" -o "$T/ff.tar.xz"
  tar -xJf "$T/ff.tar.xz" -C "$T"
  cp "$T"/ffmpeg-*/bin/ffmpeg "$T"/ffmpeg-*/bin/ffprobe bin/
  rm -rf "$T"
fi
# deno: JavaScript runtime yt-dlp needs for YouTube (in ./bin, only for this bot)
if [ ! -x bin/deno ]; then
  case "$ARCH" in x86_64) D=x86_64-unknown-linux-gnu;; aarch64) D=aarch64-unknown-linux-gnu;; esac
  echo "downloading deno into $DIR/bin"
  T=$(mktemp -d)
  curl -fsSL "https://github.com/denoland/deno/releases/latest/download/deno-$D.zip" -o "$T/deno.zip"
  python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extract('deno', sys.argv[2])" "$T/deno.zip" bin
  chmod +x bin/deno
  rm -rf "$T"
fi

# .env: asked when it is missing or BOT_TOKEN / API_ID / API_HASH are empty or wrong. Every value is checked
# and asked again when wrong; the file is written only when all are right (readable by the bot user only).
envget() { [ -f .env ] && sed -n "s/^$1=//p" .env | head -1 || true; }
valid_env() {
  [[ "$(envget BOT_TOKEN)" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]] && [[ "$(envget API_ID)" =~ ^[0-9]+$ ]] \
    && [[ "$(envget API_HASH)" =~ ^[0-9a-f]{32}$ ]]
}
if [ "${1:-}" = "--reset-env" ] || ! valid_env; then
  echo "Telegram settings (API_ID / API_HASH from https://my.telegram.org -> API development tools)"
  while :; do read -r -s -p "BOT_TOKEN (hidden): " TOK; echo
    [[ "$TOK" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]] && break; echo "  wrong: it looks like 123456789:AAE...  try again"; done
  while :; do read -r -p "API_ID (numbers only, e.g. 1234567): " AID
    [[ "$AID" =~ ^[0-9]+$ ]] && break
    [[ "$AID" =~ ^[0-9a-f]{32}$ ]] && echo "  that is the API_HASH, not the API_ID (API_ID is the short number)" || echo "  wrong: numbers only"; done
  while :; do read -r -s -p "API_HASH (32 letters/numbers, hidden): " AH; echo
    [[ "$AH" =~ ^[0-9a-f]{32}$ ]] && break; echo "  wrong: 32 characters 0-9 a-f, try again"; done
  read -r -p "ADMIN_ID (your Telegram user id, optional, Enter = skip): " ADM
  [[ "$ADM" =~ ^[0-9]*$ ]] || ADM=""
  [ -f .env ] || cp .env.example .env
  python3 - "$TOK" "$AID" "$AH" "$ADM" <<'PY'
import sys, re
tok, aid, ah, adm = sys.argv[1:]
t = open(".env").read()
for k, v in (("BOT_TOKEN", tok), ("API_ID", aid), ("API_HASH", ah), ("ADMIN_ID", adm)):
    t = re.sub(rf"(?m)^{k}=.*$", f"{k}={v}", t) if re.search(rf"(?m)^{k}=", t) else t + f"\n{k}={v}\n"
open(".env", "w").write(t)
PY
  echo "saved .env"
fi
chown -R "$USR:$USR" "$DIR"
chmod 600 .env

install -m 644 deploy/wouldmusic-bot.service deploy/wouldmusic-bot-update.service deploy/wouldmusic-bot-update.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wouldmusic-bot-update.timer
systemctl enable wouldmusic-bot.service
systemctl restart wouldmusic-bot.service
sleep 5
systemctl --no-pager --lines=15 status wouldmusic-bot.service || true
echo
echo "Done. Logs: journalctl -u wouldmusic-bot -f"
