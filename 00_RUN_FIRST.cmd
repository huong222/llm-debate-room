@echo off
cd /d "%~dp0"
python -m pip install --upgrade -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo.
  echo [ERROR] Package installation failed.
  pause
  exit /b 1
)
python -m streamlit run "%~dp0app.py"
pause
