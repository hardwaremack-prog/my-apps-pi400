@echo off
title Frostfall Cabins server
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (python server.py %*) else (py -3 server.py %*)
echo.
echo If Windows Firewall asked, allow Python on private networks so others can join.
pause
