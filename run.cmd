@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
echo LosslessCut Embed Markers
echo 1. Preview the batch - no media changes
echo 2. Apply markers - verify and back up before replacing clips
echo    Also clean up verified projects if enabled in config.local.toml
echo 3. Open the latest readable report
echo 4. Reset setup - keep config, footage, backups, and history
echo 5. Exit
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0select-action.ps1"
if errorlevel 16 exit /b 1
if errorlevel 15 exit /b 0
if errorlevel 14 goto reset_setup
if errorlevel 13 goto latest_report
if errorlevel 12 goto apply
if not errorlevel 11 exit /b 1
set "MARKER_MODE=preview"
goto process
:apply
set "MARKER_MODE=apply"
:process
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m llc_markers %MARKER_MODE%
goto finished
:latest_report
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -B llc_markers\maintenance.py report
) else (
  python -B llc_markers\maintenance.py report
)
goto finished
:reset_setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0reset-setup.ps1"
:finished
set "MARKER_RESULT=%ERRORLEVEL%"
echo.
if "%MARKER_RESULT%"=="0" echo Finished successfully.
if "%MARKER_RESULT%"=="2" echo Completed with items needing attention. Open the latest report for next steps.
if "%MARKER_RESULT%"=="1" echo Could not complete. See the error above.
if "%MARKER_RESULT%"=="130" echo Stopped. Check the latest report before rerunning.
pause
exit /b %MARKER_RESULT%
