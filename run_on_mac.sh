#!/bin/bash
# Plan B: run the scraper from your own Mac (home internet is blocked far less
# often than cloud servers), then push results so the website updates.
cd "$(dirname "$0")"
source .venv/bin/activate
git pull --ff-only
python run.py "$@"
git add data docs
git diff --cached --quiet || git commit -m "Snapshot $(date '+%Y-%m-%d %H:%M')"
git push
