@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo  AOLRC Inventory - Start App (Docker)
echo ==========================================
echo.
echo This will start the app at:
echo   http://127.0.0.1:5000/dashboard
echo.

docker compose up --build

echo.
echo App stopped.
pause