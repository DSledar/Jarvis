@echo off
title JARVIS Mark VI - Windows Background Auto-Start Installer
cd /d "%~dp0"

set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS_PATH=%~dp0start_background_silent.vbs"
set "SHORTCUT_PATH=%STARTUP_DIR%\JARVIS_Mark_VI_Background.lnk"

powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%VBS_PATH%'; $s.WorkingDirectory = '%~dp0'; $s.Description = 'JARVIS Mark VI Always-On Background Service'; $s.Save()"

echo ============================================================================
echo   JARVIS MARK VI BACKGROUND AUTO-START INSTALLED!
echo ============================================================================
echo.
echo JARVIS will now start silently in the background every time Windows boots.
echo Whenever you say "Jarvis wake up", "Wake up Jarvis", "جاڕڤس هەڵسە",
echo or press Ctrl + Alt + J, the screen will pop up and play the Stark Intro!
echo.
pause
