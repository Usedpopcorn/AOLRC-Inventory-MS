REM =========================================================
REM DB_UPGRADE_WINDOWS.bat
REM Purpose: Start Docker services and apply Alembic database
REM migrations (`flask db upgrade`) against DATABASE_URL.
REM Use this only for approved/admin migration workflows.
REM =========================================================

@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo  AOLRC Inventory - DB Upgrade (ADMIN ONLY)
echo ==========================================
echo.
echo This applies database migrations to the configured DATABASE_URL.
echo Only run if you were instructed to.
echo.

docker compose up -d --build
docker compose exec web flask db upgrade

echo.
echo Done.
pause