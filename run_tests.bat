@echo off
set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"

if exist "%PY_EXE%" (
    set "PYTHON_CMD=%PY_EXE%"
) else (
    set "PYTHON_CMD=python"
)

echo ======================================================================
echo  Running M.A.C.H. AI 110-Test Automated Verification Suite
echo ======================================================================
"%PYTHON_CMD%" run_system_verification.py
pause
