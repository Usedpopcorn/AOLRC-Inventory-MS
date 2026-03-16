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