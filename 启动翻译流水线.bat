@echo off
rem ============================================================
rem  Translation pipeline launcher  --  double click this file.
rem  (kept ASCII-only on purpose: cmd.exe reads .bat with the system
rem   ANSI codepage, so any non-ASCII byte in here would be mangled
rem   and then executed as a command -> "not a command" errors.)
rem ============================================================
cd /d "%~dp0"
chcp 65001 >nul

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  echo         Install Python 3 and make sure "python" works in a terminal.
  pause
  exit /b 1
)

if not exist "launch_pipeline.py" (
  echo [ERROR] "launch_pipeline.py" not found next to this .bat file.
  pause
  exit /b 1
)

echo Starting Translation Pipeline ... ^(0 API calls; close the window to exit^)
python "launch_pipeline.py" %*
if errorlevel 1 (
  echo.
  echo [ERROR] Pipeline GUI exited with an error - read the message above.
  pause
)
