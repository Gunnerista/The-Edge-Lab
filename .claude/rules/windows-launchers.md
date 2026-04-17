---
paths:
  - "scripts/*.bat"
  - "scripts/*.vbs"
  - "mlb/scripts/*.bat"
  - "mlb/scripts/*.vbs"
---

# Windows Launcher Rules

These rules load when Claude touches batch files or VBScript launchers. These files control how the trading system starts and restarts on Windows.

## Startup chain

```
Windows Login
  → Startup folder shortcut (WarMachine.lnk)
    → wscript scripts/run_hidden.vbs        (hidden window, no console)
      → cmd /c scripts/start_warmachine.bat  (restart loop)
        → python scripts/runner.py           (main process)
```

MLB follows the same pattern with `_mlb` suffixed files.

## Critical rule: timeout vs ping

**NEVER** use `timeout /t N /nobreak` in any `.bat` file that runs inside `run_hidden.vbs`.

- `timeout` requires a console TTY for countdown display
- Inside `wscript` hidden mode, there is no TTY → `timeout` hangs forever
- The process appears alive but does nothing (silent hang after first crash)

**ALWAYS** use `ping` for wait:
```batch
ping 127.0.0.1 -n 6 -w 1000 >nul
:: 6 pings × 1 second = ~5 second wait, no TTY required
```

### Current status

| File | Wait method | Status |
|------|------------|--------|
| `scripts/start_warmachine.bat` | `ping` | ✅ Fixed (2026-04-17) |
| `mlb/scripts/start_warmachine_mlb.bat` | `timeout` | ⚠️ **Still uses timeout** — lower priority (MLB is paper-only) |

## Line endings

- `.bat` files: Windows `cmd.exe` accepts both LF and CRLF
- Git may convert LF↔CRLF — use `cat -A` to verify (LF shows `$`, CRLF shows `^M$`)
- **IMPORTANT**: If Git Bash heredoc (`cat > file << 'EOF'`) writes a `.bat` file, it will produce LF. This works in `cmd.exe` but may cause issues with some Windows editors.
- **NEVER** let Git Bash redirect `> nul` — Bash translates it to `> /dev/null`. Use `Write` tool instead.

## VBScript structure

Both VBS files follow identical pattern:
```vbscript
Set WShell = CreateObject("WScript.Shell")
WShell.Run "cmd /c ""<full_path_to_bat>""", 0, True
```

- `0` = hidden window (SW_HIDE)
- `True` = wait for process to exit before returning
- Double-double-quotes (`""..""`) escape the inner path for `cmd /c`

## Restart loop pattern

```batch
@echo off
cd /d "C:\Users\Dell\Desktop\Projects\sports-betting-system"
:loop
python scripts\runner.py --observe-interval 60 --scan-interval 120
echo Restarting in 5 seconds...
ping 127.0.0.1 -n 6 -w 1000 >nul
goto loop
```

- `cd /d` handles cross-drive paths (though project is on C:)
- Runner exits cleanly when game window closes → loop restarts → runner sleeps until next window
- Ctrl+C in visible console breaks the loop; hidden mode requires `taskkill`

## Before editing any launcher

1. Verify runner is not currently using the file: `wmic process where "name='python.exe'" get ProcessId,CommandLine`
2. If runner is active, changes take effect only after next restart cycle (runner crash or game window close).
3. Test changes in a visible console first (`cmd /c scripts\start_warmachine.bat`), then move to hidden mode.
4. After editing, verify with `cat -A <file>` that `>nul` didn't become `>/dev/null`.

## Task Scheduler (known issue)

A stale `WarMachine` task exists in Windows Task Scheduler pointing to a wrong path (`C:\Users\Dell\Documents\Claude\Projects\배팅\...`). It fires on login but fails with `ERROR_TASK_ALREADY_RUNNING`. Needs admin rights to delete: `schtasks /Delete /TN WarMachine /F` (or `taskschd.msc` GUI).

The `warmachine_task.xml` in `scripts/` is a corrected XML config but has not been registered. The startup-folder shortcut method is currently preferred.
