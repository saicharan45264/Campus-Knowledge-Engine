@echo off
echo Running M.A.C.H. AI 110-Test Verification Suite...
python -m pytest tests/automated_verification -v
python run_system_verification.py
pause
