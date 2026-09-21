@echo off
title MultiTranslate
cd /d "%~dp0"

python -c "import flask, openpyxl, docx, requests" 2>nul
if errorlevel 1 (
  echo Installing Python packages - first run only...
  python -m pip install -r requirements.txt
)

echo Starting MultiTranslate... keep this window open, close it to stop.
start "MultiTranslate server" /min cmd /c "python web.py --lan"
timeout /t 3 >nul

set "URL=http://127.0.0.1:5055"
set "PF86=%ProgramFiles(x86)%"
set "APP="
if exist "%PF86%\Microsoft\Edge\Application\msedge.exe" set "APP=%PF86%\Microsoft\Edge\Application\msedge.exe"
if not defined APP if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "APP=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined APP if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "APP=%LocalAppData%\Google\Chrome\Application\chrome.exe"

if defined APP (
  start "" "%APP%" --app=%URL% --window-size=1180,860
) else (
  start "" %URL%
)

echo.
echo MultiTranslate window opened.  Phone (same Wi-Fi): address is shown at the top of the page.
echo.
echo Press any key to STOP MultiTranslate.
pause >nul
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5055" ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>&1
