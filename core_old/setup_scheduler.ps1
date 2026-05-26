# setup_scheduler.ps1
# Creates two Windows Task Scheduler entries for the Job Hunt Agent.
# Run once from an elevated (Administrator) PowerShell prompt:
#   Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
#   .\setup_scheduler.ps1

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python     = (Get-Command python).Source

if (-not $Python) {
    Write-Error "python not found in PATH. Install Python and try again."
    exit 1
}

# ── Task 1: Daily pipeline at 08:00 ──────────────────────────────────────────
$DailyAction  = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$ProjectDir\main.py`"" `
    -WorkingDirectory $ProjectDir

$DailyTrigger = New-ScheduledTaskTrigger -Daily -At "08:00"

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable   # run as soon as possible if missed (e.g. PC was off at 8am)

Register-ScheduledTask `
    -TaskName   "JobHuntAgent-Daily" `
    -Action     $DailyAction `
    -Trigger    $DailyTrigger `
    -Settings   $Settings `
    -RunLevel   Highest `
    -Force | Out-Null

Write-Host "Created task: JobHuntAgent-Daily (runs main.py daily at 08:00)" -ForegroundColor Green

# ── Task 2: Email watcher every 2 hours ──────────────────────────────────────
$EmailAction  = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$ProjectDir\email_watcher.py`"" `
    -WorkingDirectory $ProjectDir

# Repetition: every 2 hours, starting now, indefinitely
$EmailTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval  (New-TimeSpan -Hours 2) `
    -RepetitionDuration  ([TimeSpan]::MaxValue)

$EmailSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName   "JobHuntAgent-Email" `
    -Action     $EmailAction `
    -Trigger    $EmailTrigger `
    -Settings   $EmailSettings `
    -RunLevel   Highest `
    -Force | Out-Null

Write-Host "Created task: JobHuntAgent-Email (runs email_watcher.py every 2 hours)" -ForegroundColor Green

Write-Host ""
Write-Host "Both tasks created. Verify in Task Scheduler (taskschd.msc)."
Write-Host "To remove:  Unregister-ScheduledTask -TaskName JobHuntAgent-Daily -Confirm:`$false"
Write-Host "            Unregister-ScheduledTask -TaskName JobHuntAgent-Email  -Confirm:`$false"
