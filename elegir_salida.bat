@echo off
REM Elegir por donde habla la ingeniera (audifonos)
cd /d "%~dp0"
py -3 -m app.live_engineer --elegir-salida
pause
