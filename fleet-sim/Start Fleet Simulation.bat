@echo off
rem Double-click to start the three-tractor simulation and open the dashboard.
rem (Starts the C# fleet bridge and the Python coordinator; builds the bridge the first time.)
title Fleet Simulation
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-fleet-sim.ps1" %*
if errorlevel 1 (
  echo.
  echo Something went wrong starting the simulation. See the message above.
  pause
  exit /b 1
)
echo.
echo Dashboard: http://localhost:8080
echo To stop it, double-click "Stop Fleet Simulation.bat".
timeout /t 6 >nul
