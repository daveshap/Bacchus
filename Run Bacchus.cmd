@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m bacchus %*
) else (
    python -m bacchus %*
)
if errorlevel 1 pause
