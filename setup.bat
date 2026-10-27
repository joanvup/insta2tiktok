@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

echo ============================================
echo  insta2tiktok - Instalacion
echo ============================================
echo.

set "PY_CMD="
py -3.11 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3.11"

if not defined PY_CMD (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY_CMD=py -3"
)

if not defined PY_CMD (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD (
    echo No se encontro Python 3.11 o superior.
    echo Instala Python desde https://www.python.org/downloads/ y vuelve a ejecutar este archivo.
    echo.
    pause
    exit /b 1
)

echo Usando: %PY_CMD%

if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual en .venv ...
    %PY_CMD% -m venv .venv
    if errorlevel 1 goto :error
) else (
    echo El entorno virtual ya existe.
)

echo.
echo Instalando dependencias de Python ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Descargando Chromium para Playwright ...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto :error

echo.
echo ============================================
echo  Instalacion completada.
echo  Ejecuta run.bat para iniciar la aplicacion.
echo ============================================
echo.
pause
exit /b 0

:error
echo.
echo La instalacion fallo. Revisa los mensajes anteriores.
echo.
pause
exit /b 1
