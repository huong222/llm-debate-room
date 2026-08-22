@echo off
cd /d "%~dp0"
python -m streamlit run "%~dp0app.py"
pause
