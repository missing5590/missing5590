#!/usr/bin/env bash
# Serverda kodni GitHub'dan yangilash va qayta ishga tushirish.
set -euo pipefail
cd /opt/receipt-bot
git pull --ff-only
cd receipt-bot
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart receipt-bot
sudo systemctl --no-pager status receipt-bot | head -5
