$ErrorActionPreference = "Continue"
chcp 65001 | Out-Null
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
$python = "C:\Users\m.yeganehpour\KavirProjects\DB_GPT\.venv\Scripts\python.exe"
$config = "C:\Users\m.yeganehpour\KavirProjects\DB_GPT\configs\dbgpt-proxy-ollama-qwen.toml"
$workdir = "C:\Users\m.yeganehpour\KavirProjects\DB_GPT"
$log = "C:\Users\m.yeganehpour\KavirProjects\DB_GPT\logs\dbgpt_std.log"

Write-Host "Starting DB-GPT with Ollama models..."
Write-Host "Python: $python"
Write-Host "Config: $config"
Write-Host "Log: $log"

# Kill any existing python dbgpt webserver processes (precise match only)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*dbgpt*" -and $_.CommandLine -like "*webserver*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

# Start using Start-Process detached - launch_dbgpt.py supplies the default
# start/webserver/--config argv itself (do not regenerate it here).
$helper = "C:\Users\m.yeganehpour\KavirProjects\DB_GPT\launch_dbgpt.py"
$outLog = "C:\Users\m.yeganehpour\KavirProjects\DB_GPT\logs\dbgpt_stdout.log"
$errLog = "C:\Users\m.yeganehpour\KavirProjects\DB_GPT\logs\dbgpt_stderr.log"
$args = @("-X", "utf8", $helper)
$proc = Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $workdir -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
Write-Host "Started PID $($proc.Id)"
Start-Sleep -Seconds 5

# Check if still running
$running = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "Process still running after 5s"
    Get-Content $outLog -Tail 30 | Write-Host
    Get-Content $errLog -Tail 30 | Write-Host
} else {
    Write-Host "Process exited quickly, log tail:"
    if (Test-Path $outLog) { Get-Content $outLog -Tail 100 | Write-Host }
    if (Test-Path $errLog) { Get-Content $errLog -Tail 100 | Write-Host }
    if (Test-Path $outLog) { Write-Host "exit code check done" }
}

Write-Host "Checking port 8000..."
Start-Sleep -Seconds 25
netstat -ano | Select-String "8000" | Write-Host
Write-Host "--- dbgpt_webserver.log tail ---"
Get-Content "$workdir\logs\dbgpt_webserver.log" -Tail 50 | Write-Host

Write-Host "Trying curl localhost:8000"
try {
    $resp = curl.exe -s -m 5 http://localhost:8000/ 2>&1 | Out-String
    Write-Host "curl response length: $($resp.Length)"
    Write-Host $resp.Substring(0, [Math]::Min(500, $resp.Length))
} catch {
    Write-Host "curl failed: $_"
}
