@echo off
title SlipTrack AI - Web App Server
echo ===================================================
echo     SlipTrack AI - Slip Analyzer & Expense Tracker
echo ===================================================
echo.
cd /d "%~dp0"
echo Starting web server on http://localhost:8000 ...
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
