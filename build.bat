@echo off
setlocal EnableDelayedExpansion
title TranslateX Build Pipeline

echo.
echo ============================================================
echo   TranslateX Build Pipeline
echo   icon  ^>  PyInstaller  ^>  Inno Setup  ^>  .exe installer
echo ============================================================
echo.

:: ── Check Python ─────────────────────────────────────────────
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.9+ and add to PATH.
    goto :fail
)
for /f "tokens=*" %%i in ('python --version') do echo [INFO] Using %%i

:: ── Step 1: Install / verify dependencies ────────────────────
echo.
echo [STEP 1/4] Installing dependencies...
pip install -q -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] pip install failed. Check requirements.txt and your internet connection.
    goto :fail
)
echo [OK] Dependencies ready.

:: ── Step 2: Generate icon ────────────────────────────────────
echo.
echo [STEP 2/4] Generating icons...
python assets\create_icon.py
if %errorlevel% neq 0 (
    echo [ERROR] Icon generation failed.
    goto :fail
)
echo [OK] Icons ready.

:: ── Step 3: PyInstaller ──────────────────────────────────────
echo.
echo [STEP 3/4] Building executable with PyInstaller...

:: Install PyInstaller if missing
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] PyInstaller not found, installing...
    pip install -q pyinstaller
)

:: Clean previous build
if exist "dist\TranslateX" (
    echo [INFO] Removing old dist\TranslateX...
    rmdir /s /q "dist\TranslateX"
)
if exist "build" (
    rmdir /s /q "build"
)

pyinstaller build.spec --noconfirm
if %errorlevel% neq 0 (
    echo [ERROR] PyInstaller build failed. Check output above.
    goto :fail
)
echo [OK] Executable built: dist\TranslateX\TranslateX.exe

:: ── Step 4: Inno Setup ───────────────────────────────────────
echo.
echo [STEP 4/4] Creating installer with Inno Setup...

:: Common Inno Setup install locations
set ISCC=""
for %%p in (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    "C:\Program Files\Inno Setup 6\ISCC.exe"
    "C:\Program Files (x86)\Inno Setup 5\ISCC.exe"
    "C:\Program Files\Inno Setup 5\ISCC.exe"
) do (
    if exist %%p (
        set ISCC=%%p
        goto :found_iscc
    )
)

:: Try PATH
where ISCC.exe >nul 2>&1
if %errorlevel% equ 0 (
    set ISCC=ISCC.exe
    goto :found_iscc
)

echo [WARN] Inno Setup (ISCC.exe) not found.
echo        Download from: https://jrsoftware.org/isdl.php
echo        After installing, re-run this script to create the installer.
echo.
echo [DONE] Build complete (no installer — exe in dist\TranslateX\)
goto :success

:found_iscc
echo [INFO] Found Inno Setup: %ISCC%
if not exist "installer\Output" mkdir "installer\Output"
%ISCC% "installer\setup.iss"
if %errorlevel% neq 0 (
    echo [ERROR] Inno Setup compilation failed.
    goto :fail
)
echo [OK] Installer created: installer\Output\TranslateX_Setup_1.0.0.exe

:success
echo.
echo ============================================================
echo   BUILD SUCCESSFUL
echo ============================================================
echo.
echo   Portable app:  dist\TranslateX\TranslateX.exe
if exist "installer\Output\TranslateX_Setup_1.0.0.exe" (
echo   Installer:     installer\Output\TranslateX_Setup_1.0.0.exe
)
echo.
pause
exit /b 0

:fail
echo.
echo ============================================================
echo   BUILD FAILED — see errors above
echo ============================================================
echo.
pause
exit /b 1
