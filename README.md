# Would Music 🎶 (@Wouldmusicbot)

Telegram bot: search a song (name or YouTube link) → get it as **MP3 320 kbps** or **video 1080p / 720p / 480p / 360p**.
Files go over MTProto (pyrofork), so up to 2 GB. Everyone can use it, with limits (2 downloads at once on the
server, 1 per user, 20 per user per hour). A file sent once is cached (Telegram file_id) and sent again instantly.

**Secrets are never in git:** `BOT_TOKEN`, `API_ID`, `API_HASH` live only in `/opt/wouldmusic-bot/.env` on the VPS.

## دامەزراندن لەسەر VPS (یەکجار)

```bash
# 1) کلیلی deploy (تەنها خوێندن، تەنها بۆ ئەم repo یە)
sudo ssh-keygen -t ed25519 -N "" -f /root/.ssh/wouldmusic_deploy -C wouldmusic-vps
sudo cat /root/.ssh/wouldmusic_deploy.pub
#    ئەم دێڕە کۆپی بکە: GitHub → Wouldmusicbot → Settings → Deploy keys → Add deploy key (Allow write: نا)

# 2) دابەزاندنی کۆد
sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/wouldmusic_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
     git clone git@github.com:AndamAziz/Wouldmusicbot.git /opt/wouldmusic-bot
sudo git -C /opt/wouldmusic-bot config core.sshCommand "ssh -i /root/.ssh/wouldmusic_deploy -o IdentitiesOnly=yes"

# 3) دامەزراندن (BOT_TOKEN, API_ID, API_HASH لێرە دەپرسێت و تەنها لە .env دایاندەنێت)
cd /opt/wouldmusic-bot && sudo bash deploy/install.sh
```

API_ID / API_HASH: https://my.telegram.org → API development tools (بەخۆڕایی).

## نوێکردنەوە

```bash
cd /opt/wouldmusic-bot && sudo bash deploy/update.sh
```
yt-dlp خۆکارانە ڕۆژانە نوێ دەکرێتەوە (`wouldmusic-bot-update.timer`).

## YouTube: "Sign in to confirm you're not a bot"
ئەگەر YouTube ڕێگری لە IP ی VPS کرد: cookies ی YouTube (Netscape format، لە ئەکاونتێکی جیاواز) لە
`/opt/wouldmusic-bot/data/cookies.txt` دابنێ و `sudo chown wouldmusic: /opt/wouldmusic-bot/data/cookies.txt && sudo systemctl restart wouldmusic-bot`.

## What it touches on the VPS
Only `/opt/wouldmusic-bot` (own user `wouldmusic`, own venv, own ffmpeg/deno in `./bin` when the VPS has none) and the
systemd units `wouldmusic-bot.service` + `wouldmusic-bot-update.timer`. Memory 1.5 GB max, CPU 150 % max, low priority.
Nothing else (nginx, PHP, IPTV, other bots) is touched. Remove completely:
`sudo systemctl disable --now wouldmusic-bot wouldmusic-bot-update.timer && sudo rm -rf /opt/wouldmusic-bot /etc/systemd/system/wouldmusic-bot*`

## Commands
`/start` `/help` – help · `/song name` – in groups · `/stats` – admin only (ADMIN_ID)

## Tests
`python -m pytest -q tests`
