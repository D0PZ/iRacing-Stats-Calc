@echo off
REM Asigna el boton del volante para hablarle al ingeniero (push-to-talk)
cd /d "%~dp0"
py -3 -m app.live_engineer --asignar-boton
pause
