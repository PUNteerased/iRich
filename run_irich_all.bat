@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title iRich launcher
echo ========================================
echo  iRich - start all services
echo ========================================
echo.

if exist "%~dp0.venv\Scripts\python.exe" (
  set "PY=%~dp0.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo Python: %PY%
echo.

REM 1) Trading bot
echo [1/4] Starting bot  (src\main.py) ...
start "iRich Bot" /D "%~dp0" cmd /k ""%PY%" src\main.py"

REM 2) Telemetry API (dashboard backend)
echo [2/4] Starting telemetry API  (http://127.0.0.1:8000) ...
start "iRich Telemetry API" /D "%~dp0" cmd /k ""%PY%" scripts\telemetry_api.py"

REM 3) Next.js dashboard
echo [3/4] Starting dashboard  (http://localhost:3000) ...
start "iRich Dashboard" /D "%~dp0dashboard" cmd /k "if not exist node_modules npm install & npm run dev"

REM 4) ngrok tunnel for Vercel
where ngrok >nul 2>&1
if errorlevel 1 (
  echo [4/4] ngrok not found in PATH — skip tunnel
) else (
  echo [4/4] Starting ngrok  (beula-nonintersecting-frigidly.ngrok-free.dev) ...
  start "iRich ngrok" cmd /k "ngrok http --domain=beula-nonintersecting-frigidly.ngrok-free.dev 8000"
)

echo.
echo Opened windows:
echo   - iRich Bot
echo   - iRich Telemetry API   -^> http://127.0.0.1:8000/docs
echo   - iRich Dashboard       -^> http://localhost:3000
echo   - iRich ngrok           -^> https://beula-nonintersecting-frigidly.ngrok-free.dev
echo.
echo Close each window to stop that service.
echo.
pause
endlocal
