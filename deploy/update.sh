#!/usr/bin/env bash
# Update the bot to the latest code from GitHub:  sudo bash deploy/update.sh
set -euo pipefail
cd /opt/wouldmusic-bot
git -c safe.directory=/opt/wouldmusic-bot pull --ff-only
venv/bin/pip install -q -r requirements.txt
chown -R wouldmusic:wouldmusic /opt/wouldmusic-bot
systemctl restart wouldmusic-bot.service
systemctl --no-pager --lines=10 status wouldmusic-bot.service || true
