@echo off
rem Double-click to stop the fleet bridge and the coordinator.
title Fleet Simulation
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-fleet-sim.ps1" -Stop
timeout /t 3 >nul
