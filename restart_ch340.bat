@echo off
REM ============================================================
REM  restart_ch340.bat
REM  Recover CH340 serial port (temperature chamber) from
REM  error 31 / stuck driver WITHOUT unplugging the USB cable.
REM  Double-click to run; it self-elevates (UAC) and then calls
REM  restart_ch340.ps1 in the same folder.
REM  See docs/CH340_error31_fix.md for details.
REM ============================================================

net session >/dev/null 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_ch340.ps1"

echo.
pause
