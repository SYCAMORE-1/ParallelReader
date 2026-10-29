@echo off
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag the downloaded package folder onto restore-books.bat.
  echo Close the reader before restoring books.
  pause
  exit /b 1
)
"%~dp0runtime\python.exe" -X utf8 "%~dp0tools\restore_library_packages.py" --packages "%~1" --target "%~dp0.data"
pause
