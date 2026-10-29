@echo off
rem Daily AEGEAN award miles: collect the four legs with your logged-in profile, then publish data/daily_award.csv and
rem data/award_history.csv so the deployed app shows them. Run by hand or from Windows Task Scheduler.
rem Needs `python award_collector.py login` done once and every Chrome window using .aegean_profile closed.
rem The push uses the GITHUB_TOKEN environment variable (not stored in the repo's git config).
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate
pip install -q -r requirements.txt -r requirements-award.txt
python award_collector.py fetch ATH-FRA FRA-ATH ATH-ZRH ZRH-ATH --api
if errorlevel 1 exit /b 1
set REMOTE=https://x-access-token:%GITHUB_TOKEN%@github.com/Am015-dev/AegeanBook.git
git add data/daily_award.csv data/award_history.csv
git diff --cached --quiet && exit /b 0
git commit -m "Daily award miles %date%"
git pull --rebase %REMOTE% main && git push %REMOTE% HEAD:main
