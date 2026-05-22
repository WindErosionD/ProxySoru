@echo off
REM This file must stay ASCII-only: cmd.exe parses .bat with the system ANSI code page
REM (e.g. CP936). UTF-8 Chinese in the same file is often misread and the script can exit immediately.
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0" || (echo ERROR: cd failed & pause & exit /b 1)
if not exist "%~dp0RUN_SPEED_TEST_ASCII.bat" (
  echo ERROR: RUN_SPEED_TEST_ASCII.bat not found in the same folder.
  pause
  exit /b 1
)
call "%~dp0RUN_SPEED_TEST_ASCII.bat" %*
exit /b %errorlevel%
