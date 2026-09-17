# Wrapper invoked by the "GoldORB Tick" Windows Scheduled Task every 5
# minutes. Task Scheduler runs headless, so all output goes to a log file
# instead of the console.
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot
$logFile = Join-Path $PSScriptRoot "data\gold_tick_task.log"
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "data") | Out-Null

"[$(Get-Date -Format o)] --- gold-tick starting ---" | Out-File -FilePath $logFile -Append -Encoding utf8
& "C:\Users\raije\AppData\Local\Programs\Python\Python312\python.exe" main.py gold-tick 2>&1 |
    Out-File -FilePath $logFile -Append -Encoding utf8
"[$(Get-Date -Format o)] --- gold-tick finished (exit $LASTEXITCODE) ---" | Out-File -FilePath $logFile -Append -Encoding utf8
