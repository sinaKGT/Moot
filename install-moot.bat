@echo off
title Moot - install
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv was not found. Install it from https://docs.astral.sh/uv/
  pause
  exit /b 1
)
echo Installing Moot from this folder, in its own environment...
uv tool install --reinstall --python 3.12 "%~dp0."
if errorlevel 1 (
  echo.
  echo The installation failed; see the message above.
  pause
  exit /b 1
)
for /f "delims=" %%i in ('uv tool dir --bin') do set "MOOT_BIN=%%i"
echo.
where moot >nul 2>nul
if errorlevel 1 (
  echo Moot is installed in %MOOT_BIN%
  echo To start it by typing "moot" in any terminal, run this once: uv tool update-shell
  echo and then open a new terminal.
) else (
  echo Moot is installed. Start it any time by typing: moot
)
echo.
echo Starting Moot now. Close this window to stop it.
"%MOOT_BIN%\moot.exe"
pause
