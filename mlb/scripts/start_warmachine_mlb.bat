@echo off
:: War Machine MLB Auto-Restart Wrapper (PAPER MODE)
:: Restarts on crash with 5 second delay

cd /d "C:\Users\Dell\Desktop\Projects\sports-betting-system"

:loop
echo [%date% %time%] Starting War Machine MLB...
python mlb\scripts\runner_mlb.py

echo [%date% %time%] War Machine MLB stopped (exit code: %errorlevel%)
echo Restarting in 5 seconds... (Ctrl+C to stop)
timeout /t 5 /nobreak
goto loop
