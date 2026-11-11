@echo off
rem One-click demo: starts the SmartCity AI backend on http://127.0.0.1:8000 and opens the app.
rem Close the "SmartCity AI backend" window to stop the server.
setlocal
cd /d "%~dp0backend"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%~dp0backend\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo The .venv was not found. Set it up first, see README "Quick start".
  pause & exit /b 1
)

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (
  echo Port 8000 is already in use, so the backend is probably already running.
  goto open
)

start "SmartCity AI backend - close this window to stop" /min "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
echo Starting the backend (first start can take ~20 seconds)...
set /a tries=0
:wait
set /a tries+=1
if %tries% gtr 60 (
  echo The backend did not start. Check the "SmartCity AI backend" window for the error.
  pause & exit /b 1
)
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 ( ping -n 2 127.0.0.1 >nul & goto wait )
echo Backend is up.

:open
if "%NO_BROWSER%"=="1" exit /b 0
start "" "%~dp0frontend\index.html"
