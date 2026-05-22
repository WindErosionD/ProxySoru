@echo off
REM All-ASCII launcher. Do NOT use "goto" inside (...) blocks with cmd — can exit instantly.
REM UTF-8 console first (before any Chinese from Python or log tee).
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
cd /d "%~dp0" || (echo ERROR: cd failed & pause & exit /b 1)
if not exist "tools\bootstrap_and_run.py" (
  echo ERROR: tools\bootstrap_and_run.py not found. Run this from project root.
  pause
  exit /b 1
)

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
set SSR_BOOTSTRAP_AUTO=1

if not exist "%~dp0logs" mkdir "%~dp0logs" 2>nul
set "SSR_RUNLOG=%~dp0logs\last_bootstrap.log"
REM Log header is written by Python (UTF-8 + BOM). Do not "echo ... >" here (CP936 garbles UTF-8).
set "SSR_BOOTSTRAP_LOG=%SSR_RUNLOG%"
echo Running bootstrap (live output below; same text is appended to log)...
echo Log file: %SSR_RUNLOG%
echo.

if exist "PORTABLE_BUILD" if exist "python\python.exe" (
  set "PYTHONPATH=%~dp0"
  "python\python.exe" -u "tools\bootstrap_and_run.py" %*
  set "ERR=!errorlevel!"
  goto :after_py
)

call :find_python
if errorlevel 1 (
  echo ERROR: Python 3 not found. Install Python 3.10+ and ensure "py" or "python" is on PATH.
  echo Tip: Turn off Windows "App execution aliases" for python.exe / python3.exe if the Store opens instead.
  pause
  exit /b 1
)
if /i "!DET_KIND!"=="py" (
  py !DET_VER! -u "tools\bootstrap_and_run.py" %*
  set "ERR=!errorlevel!"
  goto :after_py
)
"!DET_EXE!" -u "tools\bootstrap_and_run.py" %*
set "ERR=!errorlevel!"
goto :after_py

:after_py
echo.
echo DONE exit_code=!ERR!
echo Full log: %SSR_RUNLOG%
if not "!ERR!"=="0" (
  echo ----- last log -----
  type "%SSR_RUNLOG%"
  echo.
)
echo Window waits 45 seconds; scroll up for errors.
timeout /t 45 /nobreak
pause
exit /b !ERR!

:find_python
py -3.11 -c "import sys" 2>nul || goto fp312
set "DET_KIND=py" & set "DET_VER=-3.11" & goto find_ok
:fp312
py -3.12 -c "import sys" 2>nul || goto fp3
set "DET_KIND=py" & set "DET_VER=-3.12" & goto find_ok
:fp3
py -3 -c "import sys" 2>nul || goto fwhere
set "DET_KIND=py" & set "DET_VER=-3" & goto find_ok
:fwhere
for /f "delims=" %%W in ('where python 2^>nul') do (
  echo %%W | findstr /i "WindowsApps" >nul
  if errorlevel 1 (
    "%%W" -c "import sys" 2>nul && (
      set "DET_KIND=exe"
      set "DET_EXE=%%W"
      goto find_ok
    )
  )
)
for /f "delims=" %%W in ('where python3 2^>nul') do (
  echo %%W | findstr /i "WindowsApps" >nul
  if errorlevel 1 (
    "%%W" -c "import sys" 2>nul && (
      set "DET_KIND=exe"
      set "DET_EXE=%%W"
      goto find_ok
    )
  )
)
exit /b 1
:find_ok
exit /b 0
