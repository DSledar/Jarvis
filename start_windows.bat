@echo off
setlocal
cd /d "%~dp0"
title JARVIS Mark VI Launcher

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python virtual environment...
  py -m venv .venv
  if errorlevel 1 goto setup_failed
)
call ".venv\Scripts\activate.bat"
if errorlevel 1 goto setup_failed
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 goto setup_failed

if not exist ".env" copy ".env.example" ".env" >nul

where ollama >nul 2>nul
if errorlevel 1 (
  echo.
  echo Note: Ollama is not installed, so local AI chat is off. That is fine.
  echo Open the "AI core" tab in JARVIS to add a free cloud brain ^(Groq / Gemini^), or install Ollama from https://ollama.com
  echo.
) else (
  echo Checking the free local model qwen2.5:7b...
  ollama pull qwen2.5:7b
)

echo Starting JARVIS Mark VI Server and Always-On Background Wake Daemon at http://127.0.0.1:8000 ...
start "JARVIS Mark VI Server" /MIN /D "%~dp0" cmd /k ".venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"
timeout /t 3 >nul

rem ---- Launch browser with unmuted autoplay policy so the Iron Man intro plays with full sound
set BROWSER_CMD=
where msedge >nul 2>nul && set BROWSER_CMD=msedge --autoplay-policy=no-user-gesture-required --start-maximized
if not defined BROWSER_CMD where chrome >nul 2>nul && set BROWSER_CMD=chrome --autoplay-policy=no-user-gesture-required --start-maximized

if defined BROWSER_CMD (
  start "" %BROWSER_CMD% "http://127.0.0.1:8000/"
) else (
  start "JARVIS Browser" "http://127.0.0.1:8000/"
)
endlocal
exit /b 0

:setup_failed
echo.
echo Setup failed. Read the error above. Check that Python 3.10+ is installed ^(tick "Add to PATH"^) and that you are online.
pause
exit /b 1
