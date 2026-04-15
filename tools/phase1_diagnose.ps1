$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location 'C:\Users\Dell\Desktop\Projects\sports-betting-system'

Write-Host "=========================================="
Write-Host "PHASE 1: Folder Diagnostic"
Write-Host "=========================================="

Write-Host ""
Write-Host "=== 1. Project root structure ==="
Get-ChildItem | Select-Object Name, @{N='Type';E={if($_.PSIsContainer){'DIR'}else{'FILE'}}} | Format-Table

Write-Host ""
Write-Host "=== Folder size + file count ==="
Get-ChildItem -Directory | ForEach-Object {
    $size = (Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1MB
    [PSCustomObject]@{
        Folder = $_.Name
        SizeMB = [math]::Round($size, 2)
        FileCount = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue).Count
    }
} | Sort-Object SizeMB -Descending | Format-Table

Write-Host ""
Write-Host "=== 2. scripts/ .py files (size + last modified) ==="
Get-ChildItem scripts\*.py | Sort-Object LastWriteTime -Descending |
    Select-Object Name,
        @{N='SizeKB';E={[math]::Round($_.Length/1KB,1)}},
        @{N='Modified';E={$_.LastWriteTime.ToString('yyyy-MM-dd HH:mm')}} |
    Format-Table

Write-Host ""
Write-Host "=== 3. ZOMBIE detection (files imported nowhere) ==="
$searchPaths = @('scripts\*.py')
if (Test-Path 'shared') { $searchPaths += 'shared\*.py' }
if (Test-Path 'mlb\scripts') { $searchPaths += 'mlb\scripts\*.py' }

$files = Get-ChildItem scripts\*.py | Where-Object { $_.Name -notmatch '^_' }
$zombies = @()
$alive = @()
foreach ($f in $files) {
    $name = $f.BaseName
    $imports = (Select-String -Path $searchPaths -Pattern "import $name|from $name" -ErrorAction SilentlyContinue).Count
    if ($imports -eq 0 -and $name -notmatch 'runner|main') {
        $zombies += $f.Name
        Write-Host "ZOMBIE: $($f.Name) (0 imports)" -ForegroundColor Red
    } else {
        $alive += $f.Name
        Write-Host "ALIVE:  $($f.Name) ($imports imports)" -ForegroundColor Green
    }
}
Write-Host ""
Write-Host "Total: $($files.Count) files | Alive: $($alive.Count) | Zombies: $($zombies.Count)" -ForegroundColor Yellow

Write-Host ""
Write-Host "=== 4. data/ folder activity (LIVE/RECENT/STALE) ==="
Get-ChildItem data\* -File -ErrorAction SilentlyContinue | ForEach-Object {
    $age = (Get-Date) - $_.LastWriteTime
    $status = if ($age.TotalHours -lt 24) { 'LIVE' }
              elseif ($age.TotalDays -lt 7) { 'RECENT' }
              else { 'STALE' }
    [PSCustomObject]@{
        File = $_.Name
        SizeMB = [math]::Round($_.Length/1MB, 2)
        LastActive = $_.LastWriteTime.ToString('yyyy-MM-dd')
        Status = $status
    }
} | Sort-Object Status, SizeMB -Descending | Format-Table

Write-Host ""
Write-Host "=== 5. Core file dependencies ==="
foreach ($core in @('runner.py','auto_trader.py','signal_engine.py','trade_engine.py')) {
    $path = "scripts\$core"
    if (Test-Path $path) {
        Write-Host ""
        Write-Host "--- $core imports ---" -ForegroundColor Cyan
        Select-String -Path $path -Pattern '^\s*(import |from )' |
            ForEach-Object { $_.Line.Trim() } |
            Where-Object { $_ -notmatch '^#' }
    } else {
        Write-Host "$core NOT FOUND" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== 6. shared/ usage (NBA vs MLB) ==="
if (Test-Path 'shared') {
    $files = Get-ChildItem shared\*.py -ErrorAction SilentlyContinue
    foreach ($f in $files) {
        $name = $f.BaseName
        $nbaPaths = @('scripts\*.py')
        $mlbPaths = @()
        if (Test-Path 'mlb\scripts') { $mlbPaths += 'mlb\scripts\*.py' }
        $nba = (Select-String -Path $nbaPaths -Pattern "shared\.$name|from shared" -ErrorAction SilentlyContinue).Count
        $mlb = if ($mlbPaths.Count -gt 0) {
            (Select-String -Path $mlbPaths -Pattern "shared\.$name|from shared" -ErrorAction SilentlyContinue).Count
        } else { 0 }
        $status = if ($nba -gt 0 -and $mlb -gt 0) { 'SHARED-OK' }
                  elseif ($nba -gt 0) { 'NBA-ONLY' }
                  elseif ($mlb -gt 0) { 'MLB-ONLY' }
                  else { 'UNUSED' }
        $color = if ($status -eq 'SHARED-OK') { 'Green' }
                 elseif ($status -eq 'UNUSED') { 'Red' }
                 else { 'Yellow' }
        Write-Host "shared/$($f.Name): NBA=$nba MLB=$mlb [$status]" -ForegroundColor $color
    }
} else {
    Write-Host "shared/ folder does not exist" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== 7. Root folder loose files ==="
Get-ChildItem -File |
    Select-Object Name,
        @{N='SizeKB';E={[math]::Round($_.Length/1KB,2)}},
        @{N='Modified';E={$_.LastWriteTime.ToString('yyyy-MM-dd')}} |
    Format-Table

Write-Host ""
Write-Host "=== 8. .bak file remnants ==="
$baks = Get-ChildItem -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '\.bak' -and $_.FullName -notmatch '\.git' }
if ($baks) {
    $baks | ForEach-Object {
        Write-Host ($_.FullName.Replace($PWD.Path + '\', ''))
    }
    Write-Host "Total .bak files: $($baks.Count)" -ForegroundColor Yellow
} else {
    Write-Host "No .bak files found" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== 9. __pycache__ folders ==="
$caches = Get-ChildItem -Recurse -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq '__pycache__' -and $_.FullName -notmatch '\.git' }
if ($caches) {
    $caches | ForEach-Object {
        Write-Host ($_.FullName.Replace($PWD.Path + '\', ''))
    }
    Write-Host "Total __pycache__ folders: $($caches.Count)" -ForegroundColor Yellow
} else {
    Write-Host "No __pycache__ folders" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== 10. System health (one-body check) ==="
Write-Host ""
Write-Host "Python processes:"
Get-Process python -ErrorAction SilentlyContinue |
    Select-Object Id, ProcessName, @{N='MemMB';E={[math]::Round($_.WorkingSet64/1MB,1)}}, StartTime |
    Format-Table

Write-Host ""
Write-Host "Files modified in last 24h (excluding cache/git/venv):"
Get-ChildItem -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $_.LastWriteTime -gt (Get-Date).AddHours(-24) -and
        $_.FullName -notmatch '__pycache__|\.git|venv'
    } |
    Select-Object @{N='Path';E={$_.FullName.Replace($PWD.Path + '\', '')}},
                  @{N='HoursAgo';E={[math]::Round(((Get-Date) - $_.LastWriteTime).TotalHours, 1)}} |
    Sort-Object HoursAgo |
    Format-Table -AutoSize

Write-Host ""
Write-Host "=========================================="
Write-Host "PHASE 1 DIAGNOSTIC COMPLETE"
Write-Host "=========================================="
Write-Host ""
Write-Host "Key indicators:"
Write-Host "  - Zombie file count (#3)"
Write-Host "  - shared/ MLB dependency (#6)"
Write-Host "  - data/ stale remnants (#4 STALE)"
Write-Host "  - Root loose files (#7)"
Write-Host "  - 24h activity files (#10) - one-body proof"
