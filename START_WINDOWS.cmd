@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM --- Use Python 3.12 if available (recommended) ---
set PY=py -3.12
%PY% --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python 3.12 not found via py launcher. Install Python 3.12 from python.org or Microsoft Store.
  echo         Then re-run this script.
  pause
  exit /b 1
)

REM --- Create venv if missing ---
if not exist "venv\Scripts\python.exe" (
  echo [1/5] Creating virtual environment...
  %PY% -m venv venv
)

REM --- Activate venv ---
call "venv\Scripts\activate.bat"

echo [2/5] Upgrading pip...
python -m pip install --upgrade pip

echo [3/5] Installing requirements...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Failed installing requirements. Run CHECK_HEALTH.cmd for diagnostics.
  pause
  exit /b 1
)

echo [4/5] Bootstrapping director account + database (safe to run multiple times)...
set DIRECTOR_EMAIL=%DIRECTOR_EMAIL%
set DIRECTOR_PASSWORD=%DIRECTOR_PASSWORD%
python BOOTSTRAP_DIRECTOR.py
if errorlevel 1 (
  echo [ERROR] Bootstrap failed.
  pause
  exit /b 1
)

echo [5/5] Starting server on http://127.0.0.1:5000
set FLASK_ENV=development
python app.py
