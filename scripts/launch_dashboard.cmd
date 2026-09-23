@echo off
cd /d "%~dp0..\dashboard"
title iRich Dashboard
if not exist "node_modules\" (
  echo Installing npm packages...
  call npm install
)
call npm run dev
echo.
echo Dashboard exited. Press any key to close.
pause >nul
