@echo off
REM Ingeniero de carrera en vivo para iRacing (abrir ANTES de entrar al auto)
cd /d "%~dp0"
start "Ingeniero iRacing" /min py -3 -m app.live_engineer --nombre Diego
