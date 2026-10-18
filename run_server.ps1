# run_server.ps1
# Kavir Motor DB-GPT launcher (replaces the old run_ollama.ps1 -- local Ollama
# support was removed from this fork entirely).
#
# - Starts the DB-GPT webserver (default config: configs/dbgpt-proxy-liara.toml)
# - Stops a previous instance that was started via launch_dbgpt.py
# - Waits for the server to answer on port 5670
# - Cleans up webserver task logs older than 14 days
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File run_server.ps1
#   powershell -ExecutionPolicy Bypass -File run_server.ps1 -Config configs/dbgpt-proxy-qwen-gateway.toml

param(
    [string]$Config = "configs/dbgpt-proxy-liara.toml",
    [int]$Port = 5670,
    [int]$TimeoutSec = 120,
    [switch]$DontWait
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

$VenvPython = "C:\Users\a.mansourpour\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "Base interpreter not found: $VenvPython" -ForegroundColor Red
    Write-Host "Edit VenvPython in this script." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $Config)) {
    Write-Host "Config not found: $Config" -ForegroundColor Red
    exit 1
}

Write-Host "Project : $ProjectDir"
Write-Host "Config  : $Config"
Write-Host "Python  : $VenvPython"
Write-Host "Port    : $Port"
Write-Host ""

# --- stop a previous instance (launched through launch_dbgpt.py only) --------
$stale = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -match 'launch_dbgpt\.py' }
foreach ($p in $stale) {
    Write-Host "Stopping previous webserver (PID $($p.ProcessId))..." -ForegroundColor Yellow
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
    } catch {
        Write-Host "Could not stop PID $($p.ProcessId): $($_.Exception.Message)" -ForegroundColor Red
    }
    Start-Sleep -Milliseconds 800
}

# --- clean old task logs ------------------------------------------------------
$logsDir = Join-Path $ProjectDir "logs"
if (Test-Path $logsDir) {
    $cutoff = (Get-Date).AddDays(-14)
    Get-ChildItem $logsDir -File -Filter "webserver_task_*.log" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $cutoff } |
        ForEach-Object { Remove-Item $_.FullName -Force; Write-Host "Removed old log: $($_.Name)" }
}

# --- warn if .env missing (launch_dbgpt.py also loads it) ---------------------
$envFile = Join-Path $ProjectDir ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "WARNING: .env not found -- providers and SQL passwords may not resolve." -ForegroundColor Yellow
}

# --- launch -------------------------------------------------------------------
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$env:PYTHONPATH = Join-Path $ProjectDir ".venv\Lib\site-packages"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }
$logOut = Join-Path $logsDir "webserver_task_$stamp.log"
$logErr = Join-Path $logsDir "webserver_task_$stamp.err.log"

Write-Host "Starting DB-GPT webserver (log: $logOut)..." -ForegroundColor Cyan
# launch_dbgpt.py passes extra argv straight to the dbgpt CLI, so the full
# command must be provided (its internal default only applies when no args
# are given at all).
$proc = Start-Process -FilePath $VenvPython -ArgumentList @('-u', '-X', 'utf8', (Join-Path $ProjectDir 'launch_dbgpt.py'), 'start', 'webserver', '--config', $Config) `
    -WorkingDirectory $ProjectDir -PassThru -RedirectStandardOutput $logOut -RedirectStandardError $logErr -WindowStyle Hidden
Write-Host "Launched PID $($proc.Id)" -ForegroundColor Green

if ($DontWait) { exit 0 }

# --- wait for the port --------------------------------------------------------
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    $up = Test-NetConnection -ComputerName 127.0.0.1 -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue
    if ($up) {
        Write-Host ""
        Write-Host "DB-GPT is UP on http://localhost:$Port (PID $($proc.Id))" -ForegroundColor Green
        exit 0
    }
    $running = $null -ne (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)
    if (-not $running) {
        Write-Host "Process exited. Last log lines:" -ForegroundColor Red
        Get-Content $logErr -Tail 25 -ErrorAction SilentlyContinue
        Get-Content $logOut -Tail 25 -ErrorAction SilentlyContinue
        exit 1
    }
}

Write-Host "Timeout: port $Port did not open within $TimeoutSec seconds (PID $($proc.Id) still running)." -ForegroundColor Yellow
exit 2
