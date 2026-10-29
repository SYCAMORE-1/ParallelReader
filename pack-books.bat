@echo off
cd /d "%~dp0"
"%~dp0runtime\python.exe" -X utf8 "%~dp0tools\package_library.py" %*
pause
