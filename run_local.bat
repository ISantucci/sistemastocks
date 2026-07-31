@echo off
setlocal EnableExtensions
title SistemaStocks - LOCAL (desarrollo)

REM ================================================================
REM  run_local.bat  -  Ejecucion LOCAL de desarrollo (SIN Docker)
REM  Uso exclusivo de desarrollo. NO tocar produccion / AWS.
REM  NO modifica el .env productivo. NO borra la base local.
REM ================================================================

REM Ubicarse siempre en la carpeta de este .bat (donde esta app.py)
cd /d "%~dp0"

REM ---- Carpetas locales (se crean si no existen; NO se borran) ----
if not exist "data_local"    mkdir "data_local"
if not exist "backups_local" mkdir "backups_local"
if not exist "logs_local"    mkdir "logs_local"

REM ================================================================
REM  Variables SOLO para desarrollo local.
REM  *** CLAVE y USUARIO locales: EXCLUSIVOS de desarrollo.       ***
REM  *** NO son las credenciales de produccion. Cambialas si     ***
REM  *** queres, pero NUNCA uses aca las de AWS.                  ***
REM ================================================================
set "FLASK_SECRET_KEY=clave_local_solo_dev_no_secreta"
set "STOCKS_DB_PATH=data_local\stocks_dev.db"
set "STOCKS_BACKUP_DIR=backups_local"
set "STOCKS_LOG_DIR=logs_local"
set "APP_HOST=0.0.0.0"
set "APP_PORT=5000"
set "APP_DEBUG=false"
set "BOOTSTRAP_ADMIN_USERNAME=adminlocal"
set "BOOTSTRAP_ADMIN_PASSWORD=clave_local_dev"

REM Seguridad: dejar EXPLICITAMENTE deshabilitado el reset destructivo.
set "ENABLE_RESET_DB="

REM ---- Verificar que exista el entorno virtual ----
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [ERROR] No se encontro el entorno virtual .venv
    echo         Ejecuta primero:  setup_local.bat
    echo.
    pause
    exit /b 1
)

echo.
echo ================================================================
echo   SistemaStocks - ENTORNO LOCAL DE PRUEBA (debug DESACTIVADO)
echo   Base local : %STOCKS_DB_PATH%   (nueva / descartable)
echo   URL local  : http://127.0.0.1:5000
echo   Usuario    : adminlocal   Password: clave_local_dev
echo   Para acceso por IP de red, ver instrucciones (ipconfig).
echo   (Ctrl+C para detener)
echo ================================================================
echo.

REM ---- Ejecutar la app con el Python del venv ----
".venv\Scripts\python.exe" app.py

REM ---- Si app.py termina con error, mantener la consola visible ----
if errorlevel 1 (
    echo.
    echo [ERROR] La aplicacion termino con un error (codigo %errorlevel%^).
    echo Revisa el mensaje de arriba.
)
echo.
echo La aplicacion se detuvo. Presiona una tecla para cerrar...
pause >nul
endlocal
