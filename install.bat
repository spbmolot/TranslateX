@echo off
echo === TranslateX — Installing dependencies ===
echo.

python --version 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Please install Python 3.9+ from python.org
    pause
    exit /b 1
)

echo Installing Python packages...
pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Failed to install some packages.
    echo If EasyOCR fails, try: pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    pause
    exit /b 1
)

echo.
echo === Installation complete! ===
echo Run:  python main.py
echo.
pause
