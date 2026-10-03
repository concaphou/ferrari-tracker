@echo off
REM One-time setup for running Ferrari Tracker on this PC. Double-click to run.
cd /d "%~dp0"
echo.
echo Setting up Ferrari Tracker. This takes about 5 minutes.
echo.
python --version || (echo Python was not found. Install it from python.org and tick "Add python.exe to PATH". & pause & exit /b 1)
git --version || (echo Git was not found. Install it from git-scm.com. & pause & exit /b 1)
git config user.name "tracker-bot"
git config user.email "tracker-bot@users.noreply.github.com"
echo.
echo Saving the updated tracker code to GitHub. If a GitHub sign-in window opens, sign in.
git add -A
git commit -m "Update tracker code for running on this PC"
git push
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
echo.
echo Scheduling runs at 11:00 am and 5:00 pm every day...
schtasks /create /f /tn "Ferrari Tracker 11am" /tr "\"%~dp0run_on_windows.bat\"" /sc daily /st 11:00
schtasks /create /f /tn "Ferrari Tracker 5pm" /tr "\"%~dp0run_on_windows.bat\"" /sc daily /st 17:00
echo.
echo Running a first snapshot now. A browser window will open and close several times.
echo This takes 20 to 40 minutes. Leave this window open.
echo.
call "%~dp0run_on_windows.bat" --force
echo.
echo Finished. Last lines of the log:
powershell -command "Get-Content tracker.log -Tail 25"
echo.
pause
