@echo off
REM Keep this .bat ASCII-only: UTF-8 Chinese in .bat is often mis-parsed by cmd (e.g. "录默认:" errors).
setlocal
cd /d "%~dp0"
echo Downloads Python embeddable and installs deps via pip. Network required; may take several minutes.
echo Default output: dist\ProxySoru_portable\ and dist\ProxySoru_portable.zip
echo.
py -3 tools\build_portable.py %*
set "EC=%errorlevel%"
echo.
if not "%EC%"=="0" (
  echo [FAILED] exit code: %EC%
) else (
  echo [OK] Portable package built.
)
pause
exit /b %EC%
