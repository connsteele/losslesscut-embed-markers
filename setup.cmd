@echo off
setlocal
cd /d "%~dp0"
if exist "G:\GPT\Temp" (
  set "TEMP=G:\GPT\Temp"
  set "TMP=G:\GPT\Temp"
)
if exist "G:\GPT\Caches" set "PIP_CACHE_DIR=G:\GPT\Caches\pip"
python --version >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 or newer is required. Install Python and enable its PATH option.
  pause
  exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
  echo Python 3.11 or newer is required.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if not exist ".venv\Scripts\python.exe" exit /b 1
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
  echo Setup failed. See the error above.
  pause
  exit /b 1
)
if not exist "config.local.toml" copy "config.example.toml" "config.local.toml" >nul

:check_media_tools
echo.
echo Checking FFmpeg and FFprobe on PATH...
set "MARKER_MEDIA_TOOLS_MISSING=0"
call :check_media_tool ffmpeg FFmpeg
call :check_media_tool ffprobe FFprobe
echo.
if "%MARKER_MEDIA_TOOLS_MISSING%"=="1" (
  echo Python setup is complete, but the media tools need attention.
  echo Install an FFmpeg build that includes both ffmpeg.exe and ffprobe.exe.
  echo Download: https://ffmpeg.org/download.html
  echo Add the folder containing both executables to your Windows PATH.
  echo Close this window, open a new terminal if needed, and run setup.cmd again.
  echo Alternatively, set full ffmpeg and ffprobe paths in config.local.toml.
  pause
  exit /b 1
)
echo Setup complete. Edit config.local.toml, then open run.cmd.
pause
exit /b 0

:check_media_tool
where %~1 >nul 2>&1
if errorlevel 1 (
  echo [MISSING] %~2 was not found on PATH.
  set "MARKER_MEDIA_TOOLS_MISSING=1"
  exit /b 0
)
call %~1 -version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] %~2 was found but could not run. Check or repair its installation.
  set "MARKER_MEDIA_TOOLS_MISSING=1"
  exit /b 0
)
echo [OK] %~2 is available:
where %~1
exit /b 0
