@echo off
cd /d "C:\Users\Dell\Desktop\Projects\sports-betting-system"
:loop
python scripts\runner.py --observe-interval 60 --scan-interval 120
echo Restarting in 5 seconds...
ping 127.0.0.1 -n 6 -w 1000 >nul
goto loop
