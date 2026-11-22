@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -m venv .venv
    if errorlevel 1 goto error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto error
)

".venv\Scripts\python.exe" -m uvicorn server:app --host 127.0.0.1 --port 8765
pause
exit /b

:error
echo Could not start AuraGen. Check that Python is installed.
pause
