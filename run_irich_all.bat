@echo off
cd /d "%~dp0"
title iRich launcher
echo ========================================
echo  iRich - start all services
echo ========================================
echo.

set "BUSY8000=0"
set "BUSY3000=0"
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>&1 && set "BUSY8000=1"
netstat -ano | findstr ":3000" | findstr "LISTENING" >nul 2>&1 && set "BUSY3000=1"

echo [1/4] Starting bot ...
start "iRich Bot" "%~dp0scripts\launch_bot.cmd"

if "%BUSY8000%"=="1" (
  echo [2/4] Telemetry API already on :8000 - skip
) else (
  echo [2/4] Starting telemetry API ...
  start "iRich Telemetry API" "%~dp0scripts\launch_api.cmd"
)

if "%BUSY3000%"=="1" (
  echo [3/4] Dashboard already on :3000 - skip
) else (
  echo [3/4] Starting dashboard ...
  start "iRich Dashboard" "%~dp0scripts\launch_dashboard.cmd"
)

REM Prefer Cloudflare Tunnel (ngrok free often hits monthly request limit ERR_NGROK_727)
where cloudflared >nul 2>&1
if not errorlevel 1 (
  tasklist /FI "IMAGENAME eq cloudflared.exe" 2>nul | findstr /I "cloudflared.exe" >nul 2>&1
  if not errorlevel 1 (
    echo [4/4] cloudflared already running - skip
  ) else (
    echo [4/4] Starting Cloudflare tunnel ...
    start "iRich Tunnel" "%~dp0scripts\launch_tunnel.cmd"
  )
  goto :done
)

where ngrok >nul 2>&1
if errorlevel 1 (
  echo [4/4] No cloudflared/ngrok in PATH - skip tunnel
  goto :done
)
tasklist /FI "IMAGENAME eq ngrok.exe" 2>nul | findstr /I "ngrok.exe" >nul 2>&1
if not errorlevel 1 (
  echo [4/4] ngrok already running - skip
  goto :done
)
echo [4/4] Starting ngrok (fallback) ...
start "iRich ngrok" "%~dp0scripts\launch_ngrok.cmd"

:done
echo.
echo URLs:
echo   Telemetry  http://127.0.0.1:8000/docs
echo   Dashboard  http://localhost:3000
echo   Tunnel     check "iRich Tunnel" window for trycloudflare.com URL
echo              then set Vercel TELEMETRY_UPSTREAM to that URL
echo.
echo Close each service window to stop it.
echo.
pause
