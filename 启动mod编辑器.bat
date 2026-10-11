@echo off
rem ============================================================
rem  MOD Tool launcher  --  double click this file to open the
rem  visual editor for NWN:EE .mod translation polishing.
rem  (kept ASCII-only on purpose: cmd.exe codepage safety)
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

if not exist "mod tool.py" (
  echo [ERROR] "mod tool.py" not found next to this .bat file.
  pause
  exit /b 1
)

echo Starting MOD Tool ... ^(close the window to exit^)
python "mod tool.py" %*
if errorlevel 1 (
  echo.
  echo [ERROR] MOD Tool exited with an error - read the message above.
  pause
)
