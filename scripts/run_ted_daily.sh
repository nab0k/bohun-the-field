#!/bin/zsh
set -e

BASE="$HOME/Downloads/bohun_tender_v4"
LOG="$HOME/Projects/bohundefence-field/logs/ted_daily.log"

mkdir -p "$(dirname "$LOG")"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"

/usr/bin/python3 "$BASE/tender_v4.py" \
  --db "$BASE/tenders.sqlite3" \
  --out "$BASE/tenders_v4.csv" \
  --since "$(date -v-14d +%Y%m%d)" \
  --limit 100 \
  >> "$LOG" 2>&1
