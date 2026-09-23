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

REM --- helpers: skip if port already listening ---
call :port_in_use 8000
set "API_BUSY=%ERRORLEVEL%"
call :port_in_use 3000
set "DASH_BUSY=%ERRORLEVEL%"

REM 1) Trading bot (skip if main.py already running)
call :proc_running "src\main.py"
if errorlevel 1 (
  echo [1/4] Bot already running — skip
) else (
  echo [1/4] Starting bot  (src\main.py) ...
  start "iRich Bot" /D "%~dp0" cmd /k ""%PY%" src\main.py"
)

REM 2) Telemetry API
if "%API_BUSY%"=="1" (
  echo [2/4] Telemetry API already on :8000 — skip
) else (
  echo [2/4] Starting telemetry API  (http://127.0.0.1:8000) ...
  start "iRich Telemetry API" /D "%~dp0" cmd /k ""%PY%" scripts\telemetry_api.py"
)

REM 3) Next.js dashboard
if "%DASH_BUSY%"=="1" (
  echo [3/4] Dashboard already on :3000 — skip
) else (
  echo [3/4] Starting dashboard  (http://localhost:3000) ...
  start "iRich Dashboard" /D "%~dp0dashboard" cmd /k "if not exist node_modules npm install & npm run dev"
)

REM 4) ngrok
where ngrok >nul 2>&1
if errorlevel 1 (
  echo [4/4] ngrok not found in PATH — skip tunnel
) else (
  call :proc_running "ngrok"
  if errorlevel 1 (
    echo [4/4] ngrok already running — skip
  ) else (
    echo [4/4] Starting ngrok  (beula-nonintersecting-frigidly.ngrok-free.dev) ...
    start "iRich ngrok" cmd /k "ngrok http --domain=beula-nonintersecting-frigidly.ngrok-free.dev 8000"
  )
)

echo.
echo URLs:
echo   Telemetry  http://127.0.0.1:8000/docs
echo   Dashboard  http://localhost:3000
echo   ngrok      https://beula-nonintersecting-frigidly.ngrok-free.dev
echo.
echo Close each service window to stop it.
echo.
pause
endlocal
exit /b 0

:port_in_use
REM returns 1 if listening, 0 if free
netstat -ano | findstr /R /C:":%~1 .*LISTENING" >nul 2>&1
if errorlevel 1 (exit /b 0) else (exit /b 1)

:proc_running
REM returns 1 if command line contains arg, 0 otherwise
wmic process where "CommandLine like '%%%~1%%'" get ProcessId 2>nul | findstr /R "[0-9]" >nul 2>&1
if errorlevel 1 (exit /b 0) else (exit /b 1)
