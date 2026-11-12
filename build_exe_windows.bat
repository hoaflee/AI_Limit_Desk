@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start_windows.bat first to install dependencies.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pyinstaller
if errorlevel 1 (pause & exit /b 1)
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm --onefile --windowed --add-data "assets;assets" --name AI_Limit_Desk_V5 --collect-submodules PIL --hidden-import PIL._tkinter_finder main.py
if errorlevel 1 (pause & exit /b 1)
echo Done: dist\AI_Limit_Desk_V5.exe
pause
endlocal
