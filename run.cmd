@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.cmd first.
  pause
  exit /b 1
)
echo LosslessCut Embed Markers
echo 1. Preview the batch - no media changes
echo 2. Apply markers - verify and back up before replacing clips
echo    Also clean up verified projects if enabled in config.local.toml
echo 3. Exit
choice /c 123 /n /m "Choose 1, 2, or 3: "
if errorlevel 3 exit /b 0
if errorlevel 2 (
  ".venv\Scripts\python.exe" -m llc_markers apply
) else (
  ".venv\Scripts\python.exe" -m llc_markers preview
)
set "MARKER_RESULT=%ERRORLEVEL%"
echo.
echo Finished with exit code %MARKER_RESULT%. See the report path above.
pause
exit /b %MARKER_RESULT%
