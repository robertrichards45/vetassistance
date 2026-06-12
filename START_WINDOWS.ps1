Set-Location -Path $PSScriptRoot

try { py -0p | Out-Null } catch {
  Write-Host "ERROR: Python launcher (py.exe) not found. Install Python 3.12: winget install Python.Python.3.12" -ForegroundColor Red
  exit 1
}

try { py -3.12 -c "import sys; print(sys.version)" | Out-Null } catch {
  Write-Host "ERROR: Python 3.12 not found. Install it: winget install Python.Python.3.12" -ForegroundColor Red
  exit 1
}

if (-not (Test-Path "venv")) { py -3.12 -m venv venv }

& .\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

python .\healthcheck.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python .\app.py
