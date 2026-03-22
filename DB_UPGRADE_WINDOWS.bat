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

for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "CURRENT_BRANCH=%%B"
if not defined CURRENT_BRANCH (
  echo ERROR: Unable to determine current git branch.
  echo Make sure this command is run inside the repository.
  echo.
  pause
  exit /b 1
)

if /I not "%CURRENT_BRANCH%"=="main" (
  echo ERROR: Refusing DB upgrade on non-main branch.
  echo Current branch: %CURRENT_BRANCH%
  echo.
  echo Switch to main first, then run this script again.
  echo.
  pause
  exit /b 1
)

echo Branch check passed: %CURRENT_BRANCH%
echo.

docker compose up -d --build
docker compose exec web flask db upgrade

echo.
echo Done.
pause