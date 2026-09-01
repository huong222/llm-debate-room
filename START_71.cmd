@echo off
cd /d "%~dp0"
echo Starting LLM Debate Room proto-1.1234571...
python -m streamlit run "%~dp0app.py"
pause
