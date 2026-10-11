@echo off
rem ============================================================
rem  TLK Tool launcher  --  double click this file to open the
rem  visual editor for NWN:EE .tlk files (audit / fill / ledger).
rem  (kept ASCII-only on purpose: cmd.exe reads .bat with the system
rem   ANSI codepage, so any non-ASCII byte in here would be mangled
rem   and then executed as a command -> "not a command" errors.)
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  echo         Install Python 3 and make sure "python" works in a terminal.
  pause
  exit /b 1
)

if not exist "tlk tool.py" (
  echo [ERROR] "tlk tool.py" not found next to this .bat file.
  pause
  exit /b 1
)

echo Starting TLK Tool ... ^(close the window to exit^)
python "tlk tool.py" %*
if errorlevel 1 (
  echo.
  echo [ERROR] TLK Tool exited with an error - read the message above.
  pause
)
