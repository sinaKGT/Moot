@echo off
title Moot - server
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv was not found. Install it from https://docs.astral.sh/uv/
  pause
  exit /b 1
)
uv run moot serve --open
echo.
pause
