@echo off
cd /d "%~dp0.."
title iRich Tunnel (Cloudflare)
if not exist "logs" mkdir logs

where cloudflared >nul 2>&1
if errorlevel 1 (
  echo cloudflared not found. Install: winget install Cloudflare.cloudflared
  pause
  exit /b 1
)

echo Starting Cloudflare quick tunnel -^> http://127.0.0.1:8000
echo.
echo Copy the https://....trycloudflare.com URL from the log below
echo Set it on Vercel as TELEMETRY_UPSTREAM then Redeploy.
echo.
cloudflared tunnel --url http://127.0.0.1:8000
echo.
echo Tunnel exited. Press any key to close.
pause >nul
