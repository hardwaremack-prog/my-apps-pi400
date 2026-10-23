@echo off
cd /d "%~dp0"
where python >nul 2>nul || (echo Python is not installed. Get it from https://www.python.org/downloads/ & pause & exit /b)
echo PiMesh demo - 6 simulated Pis. Close this window to stop.
python pimesh.py --demo --open --config pimesh.example.json --mesh demo
pause
