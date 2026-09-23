@echo off
cd /d "%~dp0.."
title iRich Telemetry API
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\telemetry_api.py
) else (
  python scripts\telemetry_api.py
)
echo.
echo API exited. Press any key to close.
pause >nul
