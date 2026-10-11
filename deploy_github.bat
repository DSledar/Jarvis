@echo off
title JARVIS GitHub Deployment
cd /d "%~dp0"

echo ========================================================
echo               JARVIS GITHUB DEPLOYMENT SCRIPT
echo ========================================================
echo.

git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Git is not installed or not in PATH.
    echo Please install Git from https://git-scm.com/
    pause
    exit /b 1
)

if not exist ".git" (
    echo Initializing local Git repository...
    git init
    git branch -M main
)

echo Adding modified files to Git...
git add .

set /p commit_msg="Enter commit message (Press Enter for default): "
if "%commit_msg%"=="" set commit_msg="Update JARVIS Free Agent v4.5"

git commit -m "%commit_msg%"

echo.
git remote -v
echo.
set /p has_remote="Do you already have a remote configured? (Y/N): "
if /i "%has_remote%"=="N" (
    echo.
    set /p repo_url="Enter your GitHub repository URL (e.g. https://github.com/USER/REPO.git): "
    if not "%repo_url%"=="" (
        git remote remove origin >nul 2>&1
        git remote add origin %repo_url%
    )
)

echo.
echo Pushing to GitHub (main branch)...
git push -u origin main

echo.
echo ========================================================
echo Deployment process finished.
echo ========================================================
pause
