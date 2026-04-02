@echo off
:: War Machine Auto-Restart Wrapper (LIVE MODE)
:: Restarts on crash with 5 second delay
:: Place shortcut in shell:startup for boot autostart

cd /d "C:\Users\Dell\Desktop\Projects\sports-betting-system"

:loop
echo [%date% %time%] Starting War Machine (LIVE)...
python scripts\runner.py --observe-interval 60 --scan-interval 120

echo [%date% %time%] War Machine stopped (exit code: %errorlevel%)
echo Restarting in 5 seconds... (Ctrl+C to stop)
timeout /t 5 /nobreak
goto loop
