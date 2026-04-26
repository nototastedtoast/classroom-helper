@echo off
setlocal enabledelayedexpansion
title Tro Ly Lop Hoc
cd /d "%~dp0"

:: Pick Python — prefer venv, then venv312, fall back to system
if exist venv\Scripts\python.exe (
    set PY=venv\Scripts\python.exe
    set PIP=venv\Scripts\python.exe -m pip
) else if exist venv312\Scripts\python.exe (
    set PY=venv312\Scripts\python.exe
    set PIP=venv312\Scripts\python.exe -m pip
) else (
    set PY=python
    set PIP=python -m pip
)

:menu
cls
echo ============================================================
echo   Tro Ly Lop Hoc
echo ============================================================
echo.
echo   1  Run app               (overlay, always on top)
echo   2  Install dependencies  (run once after cloning)
echo   3  Build .exe            (takes 5-15 min, needs PyInstaller)
echo   4  Exit
echo.
set /p choice="Choose [1-4]: "

if "%choice%"=="1" goto :overlay
if "%choice%"=="2" goto :install
if "%choice%"=="3" goto :build
if "%choice%"=="4" exit /b
goto :menu

:overlay
echo [*] Starting overlay...
%PY% overlay.py
goto :menu

:install
echo [*] Creating venv with current Python...
python -m venv venv
set PY=venv\Scripts\python.exe
set PIP=venv\Scripts\python.exe -m pip
echo [*] Installing packages...
%PIP% install flask python-dotenv anthropic openai faster-whisper ^
    sounddevice numpy customtkinter requests pywin32 pillow ^
    edge-tts keyboard --quiet
echo [OK] Done.
pause
goto :menu

:build
echo [*] Building .exe (this takes 5-15 minutes)...
%PIP% install pyinstaller --quiet
%PY% -m PyInstaller --onedir --windowed --name "TroLyLopHoc" ^
    --add-data "templates;templates" --add-data "skills;skills" ^
    --add-data "SOUL.md;." --add-data "config.toml;." ^
    --hidden-import "customtkinter" --hidden-import "flask" ^
    --hidden-import "anthropic" --hidden-import "openai" ^
    --hidden-import "faster_whisper" --hidden-import "sounddevice" ^
    --hidden-import "edge_tts" --hidden-import "keyboard" ^
    --collect-all "customtkinter" --collect-all "faster_whisper" ^
    --noconfirm overlay.py
if exist dist\TroLyLopHoc\TroLyLopHoc.exe (
    echo [OK] Built: dist\TroLyLopHoc\TroLyLopHoc.exe
    echo      Copy your .env into dist\TroLyLopHoc\ before sharing.
) else (
    echo [ERR] Build failed - check output above.
)
pause
goto :menu
