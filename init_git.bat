@echo off
echo Initializing Git Repository...
git init
if %errorlevel% neq 0 (
    echo Error: Git is still not recognized. Please restart VS Code/Terminal and try again.
    pause
    exit /b
)

echo Adding files...
git add .
git commit -m "Initial commit for Majima AI"

echo.
echo Git repository initialized successfully!
echo.
echo Next steps:
echo 1. Create a new repository on GitHub.com
echo 2. Run the commands shown on GitHub, for example:
echo    git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
echo    git branch -M main
echo    git push -u origin main
echo.
pause
