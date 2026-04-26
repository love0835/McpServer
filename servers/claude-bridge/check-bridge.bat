@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0check-bridge.ps1"
if errorlevel 1 (
  echo.
  echo [Script ended with error level %errorlevel%]
  pause
)
