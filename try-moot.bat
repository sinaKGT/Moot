@echo off
title Moot live check
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv was not found. Install it from https://docs.astral.sh/uv/
  pause
  exit /b 1
)
uv run python scripts\live_check.py
echo.
pause
