@echo off
cd /d "%~dp0"
"%~dp0runtime\python.exe" -X utf8 "%~dp0tools\verify_release.py"
pause
