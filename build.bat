@echo off
setlocal EnableDelayedExpansion

set EXE_NAME=Buckshot-DoH-v2.4.1
set PYTHON=py -3.14 -m PyInstaller

echo ============================================
echo   DoH DNS Manager - Build EXE (PyInstaller)
echo ============================================

echo.
echo [1/3] Cleaning previous build...
if exist "dist\%EXE_NAME%.exe" del /f /q "dist\%EXE_NAME%.exe" >nul 2>nul
if exist build rmdir /s /q build >nul 2>nul
if exist "%EXE_NAME%.spec" del "%EXE_NAME%.spec" >nul 2>nul

echo.
echo [2/3] Building single EXE...

%PYTHON% main.py ^
  --onefile ^
  --windowed ^
  --name "%EXE_NAME%" ^
  --manifest app.manifest ^
  --add-data "app;app" ^
  --hidden-import "customtkinter" ^
  --hidden-import "pystray" ^
  --hidden-import "pystray._win32" ^
  --hidden-import "PIL" ^
  --hidden-import "PIL._tkinter_finder" ^
  --hidden-import "winreg" ^
  --collect-all "customtkinter" ^
  --collect-all "pystray" ^
  --exclude-module "matplotlib" ^
  --exclude-module "numpy" ^
  --exclude-module "scipy" ^
  --exclude-module "pandas" ^
  --noupx ^
  --clean

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Build failed!
    pause
    exit /b 1
)

echo.
echo [3/3] Done!
echo.
echo   File: dist\%EXE_NAME%.exe
echo   Run as Administrator (UAC prompt is automatic on double-click).
echo.
explorer dist
pause
