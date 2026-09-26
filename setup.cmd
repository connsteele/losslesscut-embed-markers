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
echo Setup complete. Edit config.local.toml, then open run.cmd.
echo FFmpeg and FFprobe must be installed separately or configured with full paths.
pause
