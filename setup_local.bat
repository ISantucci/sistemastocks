@echo off
setlocal EnableExtensions
title SistemaStocks - setup local

REM ================================================================
REM  setup_local.bat  -  Preparacion inicial del entorno LOCAL
REM  Crea .venv, actualiza pip, instala requirements y carpetas.
REM  SIN Docker. NO toca AWS ni la base de produccion.
REM ================================================================

cd /d "%~dp0"

echo ================================================================
echo   Preparacion del entorno LOCAL de desarrollo (sin Docker)
echo ================================================================
echo.

REM ---- Verificar Python (primero el launcher 'py', luego 'python') ----
set "PYCMD="
py --version >nul 2>&1 && set "PYCMD=py"
if not defined PYCMD (
    python --version >nul 2>&1 && set "PYCMD=python"
)
if not defined PYCMD (
    echo [ERROR] No se encontro Python. Instala Python 3.x desde python.org
    echo         y asegurate de marcar "Add Python to PATH".
    pause
    exit /b 1
)
echo Usando interprete: %PYCMD%
echo.

REM ---- Crear entorno virtual .venv si no existe ----
if exist ".venv\Scripts\python.exe" (
    echo El entorno .venv ya existe. Se reutiliza.
) else (
    echo Creando entorno virtual .venv ...
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual .venv
        pause
        exit /b 1
    )
)

REM ---- Actualizar pip ----
echo.
echo Actualizando pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] No se pudo actualizar pip.
    pause
    exit /b 1
)

REM ---- Instalar dependencias ----
echo.
echo Instalando dependencias de requirements.txt ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Fallo la instalacion de dependencias.
    pause
    exit /b 1
)

REM ---- Crear carpetas locales ----
if not exist "data_local"    mkdir "data_local"
if not exist "backups_local" mkdir "backups_local"
if not exist "logs_local"    mkdir "logs_local"

echo.
echo ================================================================
echo   Listo. Entorno local preparado correctamente.
echo   Para iniciar el sistema ejecuta:  run_local.bat
echo ================================================================
pause
endlocal
