@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

if not exist ".venv\Scripts\python.exe" (
    echo No se encontro el entorno virtual.
    echo Ejecuta setup.bat primero.
    echo.
    pause
    exit /b 1
)

echo Iniciando insta2tiktok en http://127.0.0.1:8000
echo Cierra esta ventana o pulsa Ctrl+C para detener el servidor.
echo.

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:8000'"

".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-server-header

echo.
echo El servidor se detuvo.
pause
