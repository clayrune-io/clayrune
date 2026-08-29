@echo off
REM Clayrune boot launcher (Windows) - headless, no browser.
REM
REM Entry point for the "Clayrune" scheduled task created by
REM tools\install-autostart.ps1. It differs from start.bat in ways that only
REM matter at boot:
REM
REM   1. It never opens a browser. At boot the task runs before anyone logs in,
REM      so there is no desktop session to draw a window on. Open the dashboard
REM      yourself from the Desktop shortcut or http://localhost:5199.
REM   2. It never pauses or prompts - there is no console to read a prompt, so
REM      any wait-for-input would hang the task invisibly forever.
REM   3. It logs to its OWN file, data\logs\clayrune-boot.log, and NOT to
REM      clayrune.log. This is not cosmetic. start.bat / start-hidden.vbs hold
REM      clayrune.log open with a share mode that denies a second writer (the
REM      same lock the restart path hit on 2026-07-27, see the fallback in
REM      mc\blueprints\system_routes.py). When cmd cannot open a >> target it
REM      prints an error, SKIPS the command, and leaves ERRORLEVEL at 0 - so a
REM      contended log made this task report success while starting nothing at
REM      all. Owning the file removes that failure mode entirely.
REM
REM Measured 2026-08-29: with a Clayrune already running, this script logged
REM "starting server" and "server exited rc=0" 0.00s apart, having launched
REM nothing. That is what the dedicated log file fixes.

title Clayrune (boot)

setlocal

REM Resolve the install directory (parent of this script's directory).
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
for %%I in ("%SCRIPT_DIR%") do set "CLAYRUNE_DIR=%%~dpI"
set "CLAYRUNE_DIR=%CLAYRUNE_DIR:~0,-1%"

cd /d "%CLAYRUNE_DIR%"

if not exist "data\logs" mkdir "data\logs"
set "BOOTLOG=%CLAYRUNE_DIR%\data\logs\clayrune-boot.log"

if not exist ".venv\Scripts\activate.bat" (
    echo [%DATE% %TIME%] no .venv found at %CLAYRUNE_DIR%\.venv - re-run the installer >> "%BOOTLOG%"
    exit /b 1
)

call ".venv\Scripts\activate.bat"

REM Don't start a second Clayrune on top of a live one. server.py has its own
REM port-conflict guard, but that guard bind-tests 0.0.0.0 and on Windows a
REM second bind can SUCCEED against a listener that set SO_REUSEADDR - observed
REM 2026-08-29, when a full second instance booted and announced itself on 5199
REM alongside the live one. Two MCs on one port split traffic between separate
REM agent_sessions dicts. A connect probe answers the question the bind test
REM cannot: is something already serving here? Port resolves the same way
REM server.py resolves it (MC_PORT, then config.json, then 5199).
python -c "import json,os,socket,sys; p=int(os.environ.get('MC_PORT') or (json.load(open('config.json')).get('port') or 5199)); s=socket.create_connection(('127.0.0.1',p),2); s.close()" 2>nul
if not errorlevel 1 (
    echo [%DATE% %TIME%] a Clayrune is already serving on this port - nothing to do >> "%BOOTLOG%"
    exit /b 0
)

echo [%DATE% %TIME%] starting server (headless, boot task) >> "%BOOTLOG%"
python server.py >> "%BOOTLOG%" 2>&1
echo [%DATE% %TIME%] server exited rc=%ERRORLEVEL% >> "%BOOTLOG%"
exit /b %ERRORLEVEL%
