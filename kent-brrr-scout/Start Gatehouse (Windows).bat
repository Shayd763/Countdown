@echo off
REM Gatehouse - Windows launcher. Double-click to start.
REM First run sets everything up (2-5 minutes); after that it opens in a few seconds.
cd /d "%~dp0"

set PY=
where py >nul 2>nul && (py -3.13 -c "1" >nul 2>nul && set "PY=py -3.13")
if not defined PY (where py >nul 2>nul && (py -3.12 -c "1" >nul 2>nul && set "PY=py -3.12"))
if not defined PY (python -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul && set "PY=python")
if not defined PY (
  echo Python 3.12 or newer is needed. Install it from https://www.python.org/downloads/
  echo ^(tick "Add python.exe to PATH" during install^) and run this again.
  pause
  exit /b 1
)

if not exist .venv (
  echo First run: creating a private Python environment...
  %PY% -m venv .venv || (pause & exit /b 1)
)
call .venv\Scripts\activate.bat

if not exist .venv\.installed (
  echo Installing components...
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -e . || (pause & exit /b 1)
  if not exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
    if not exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" (
      echo Google Chrome not found - downloading a browser for the portal collector...
      python -m playwright install chromium
    )
  )
  echo done> .venv\.installed
)

if not exist .env copy .env.example .env >nul

REM Already running in the background (python -m src.scheduler install-app)? Just open it.
python -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1',8000))==0 else 1)"
if not errorlevel 1 (
  start "" http://127.0.0.1:8000/
  exit /b 0
)

echo.
echo Gatehouse is running at http://127.0.0.1:8000  (keep this window open; close it to stop)
python -m src.web --open
pause
