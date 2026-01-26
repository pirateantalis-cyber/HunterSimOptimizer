@echo off
echo ====================================
echo Hunter Sim - Multi-Hunter Optimizer
echo ====================================
echo.

cd /d "%~dp0"

REM Use venv if available (for Rust support)
if exist ".venv\Scripts\python.exe" (
    echo Using virtual environment with Rust support...
    .venv\Scripts\python.exe hunter-sim\gui_multi.py
) else (
    cd hunter-sim
    echo Starting Multi-Hunter GUI...
    python gui_multi.py
)

if errorlevel 1 (
    echo.
    echo Error running the GUI. Make sure Python 3.10+ is installed.
    echo Try running: python --version
    pause
)
