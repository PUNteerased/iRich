@echo off
cd /d "%~dp0.."
title iRich Bot
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" src\main.py
) else (
  python src\main.py
)
echo.
echo Bot exited. Press any key to close.
pause >nul
