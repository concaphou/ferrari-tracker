@echo off
REM Runs one Ferrari Tracker snapshot on this PC and sends the results to GitHub.
REM Task Scheduler starts this at 11am and 5pm. Double-click setup_windows.bat first.
cd /d "%~dp0"
set PYTHONUTF8=1
set TRACKER_HEADFUL=1
echo ===== %date% %time% ===== >> tracker.log
git pull --ff-only >> tracker.log 2>&1
call .venv\Scripts\activate.bat
python run.py %* >> tracker.log 2>&1
git add data docs
git diff --cached --quiet || git commit -m "Snapshot %date% %time%" >> tracker.log 2>&1
git push >> tracker.log 2>&1
