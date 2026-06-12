@echo off
setlocal
cd /d "%~dp0"

set PY=py -3.12
%PY% --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python 3.12 not found. Run: py -0p to list installed versions.
  exit /b 1
)

if not exist "venv\Scripts\python.exe" (
  echo [INFO] No venv found. Creating...
  %PY% -m venv venv
)

call "venv\Scripts\activate.bat"

echo [A] Python:
python --version

echo [B] compileall:
python -m compileall app
if errorlevel 1 (
  echo [ERROR] Syntax errors detected.
  exit /b 1
)

echo [C] healthcheck:
python healthcheck.py
if errorlevel 1 (
  echo [ERROR] Healthcheck failed.
  exit /b 1
)

echo [OK] Looks good.
