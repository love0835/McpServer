$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "=== Claude MCP Bridge Health Check ===" -ForegroundColor Cyan
Write-Host ""

$listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue

if (-not $listener) {
    Write-Host "[X] Server is NOT running, auto-launching..." -ForegroundColor Yellow
    $manager = "E:\McpServer\McpServerManager\McpServerManager.exe"
    Start-Process $manager
    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Seconds 1
        $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
        if ($listener) {
            Write-Host "[OK] Started! PID $($listener.OwningProcess) (took $($i+1)s)" -ForegroundColor Green
            break
        }
    }
    if (-not $listener) {
        Write-Host "[X] Did not come up in 10s. Check E:\McpServer\servers\claude-bridge\logs\stderr.log" -ForegroundColor Red
    }
} else {
    $proc = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    Write-Host "[OK] Server running, PID $($listener.OwningProcess) ($($proc.Name))" -ForegroundColor Green
    if ($proc -and $proc.StartTime) {
        $uptime = (Get-Date) - $proc.StartTime
        Write-Host "     Uptime: $([int]$uptime.TotalMinutes) min" -ForegroundColor DarkGray
    }

    try {
        $body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"hc","version":"1"}}}'
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/mcp" -Method POST -Body $body -ContentType "application/json" -Headers @{"Accept"="application/json, text/event-stream"} -UseBasicParsing -TimeoutSec 5
        $session = $r.Headers["mcp-session-id"]
        $sessShort = if ($session) { $session.Substring(0, 8) } else { "n/a" }
        Write-Host "[OK] MCP endpoint responding (HTTP $($r.StatusCode), session $sessShort...)" -ForegroundColor Green

        $listBody = '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
        $r2 = Invoke-WebRequest -Uri "http://127.0.0.1:8000/mcp" -Method POST -Body $listBody -ContentType "application/json" -Headers @{"Accept"="application/json, text/event-stream"; "mcp-session-id"=$session} -UseBasicParsing -TimeoutSec 5
        $names = ([regex]::Matches($r2.Content, '"name":"([^"]+)"')) | ForEach-Object { $_.Groups[1].Value }
        Write-Host "[OK] Tools registered: $($names -join ', ')" -ForegroundColor Green
    } catch {
        Write-Host "[!] Listener up but endpoint failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Press any key to close..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
