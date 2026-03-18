@echo off
setlocal EnableExtensions EnableDelayedExpansion

title Neuro-slice Full Environment Setup Launcher

set "SCRIPT_DIR=%~dp0"
set "PS1_FILE=%SCRIPT_DIR%setup.ps1"

echo ==========================================
echo   Neuro-slice Windows Full Setup Launcher
echo ==========================================
echo.

if not exist "%PS1_FILE%" (
    echo [ERROR] Missing installer script:
    echo   "%PS1_FILE%"
    echo.
    echo Please make sure setup.ps1 exists in the same folder as this file.
    echo.
    pause
    exit /b 1
)

where powershell >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Windows PowerShell was not found on PATH.
    echo.
    echo Try opening PowerShell manually and run:
    echo   powershell -ExecutionPolicy Bypass -File "%PS1_FILE%"
    echo.
    pause
    exit /b 1
)

echo This launcher will run the full project setup:
echo   "%PS1_FILE%"
echo.
echo It is intended to provision:
echo   - the main project environment
echo   - optional project dependencies used by the app
echo   - the legacy detector environment
echo   - runtime wiring files used by the application
echo.
echo If Windows asks for permission, choose to continue.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1_FILE%"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo [OK] Full environment setup finished successfully.
) else (
    echo [ERROR] Setup failed with exit code %EXIT_CODE%.
)

echo.
pause
exit /b %EXIT_CODE%