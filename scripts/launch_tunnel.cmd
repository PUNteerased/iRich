@echo off
cd /d "%~dp0.."
title iRich Tunnel (Cloudflare)
if not exist "logs" mkdir logs

where cloudflared >nul 2>&1
if errorlevel 1 (
  echo cloudflared not found. Install:
  echo   winget install Cloudflare.cloudflared
  pause
  exit /b 1
)

REM Named tunnel (stable URL): put token in scripts\cloudflare_tunnel.token
REM Create token in Zero Trust -^> Networks -^> Tunnels -^> your tunnel -^> Install connector
set "TOKEN_FILE=%~dp0cloudflare_tunnel.token"
if exist "%TOKEN_FILE%" (
  for /f "usebackq delims=" %%T in ("%TOKEN_FILE%") do (
    echo Starting NAMED Cloudflare tunnel (stable hostname)...
    cloudflared tunnel run --token %%T
    goto :end
  )
)

echo No scripts\cloudflare_tunnel.token found.
echo Falling back to QUICK tunnel (URL changes every restart).
echo.
echo For a permanent URL: Zero Trust -^> Networks -^> Tunnels
echo then save the connector token into:
echo   %TOKEN_FILE%
echo.
cloudflared tunnel --url http://127.0.0.1:8000

:end
echo.
echo Tunnel exited. Press any key to close.
pause >nul
