@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [AI Limit Desk V5] Initial Python environment setup...
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv .venv
  ) else (
    python -m venv .venv
  )
  if errorlevel 1 (
    echo Python 3.10+ is required. Install Python and try again.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
  echo Cannot install dependencies. Run debug_windows.bat for details.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%CD%\main.py"
endlocal
