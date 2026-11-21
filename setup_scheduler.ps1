<#
.SYNOPSIS
  Registers or manages the two Windows Task Scheduler jobs for LeadPipeline:
    1) LeadPipeline-DailyAutopilot  -> Daily at 3:00 PM (+ automatic catch-up when PC boots/comes online!)
    2) LeadPipeline-ReplyWatcher    -> Every 30 minutes (instant hot-reply alerts + AI closing drafts to your ALERT_EMAIL)

.EXAMPLE
  .\setup_scheduler.ps1             # Install / update both scheduled tasks
  .\setup_scheduler.ps1 -Status     # Check current task status & next run times
  .\setup_scheduler.ps1 -Uninstall  # Remove both scheduled tasks
#>
param(
    [switch]$Status,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonWExe = Join-Path $RepoDir "venv\Scripts\pythonw.exe"
$AutopilotScript = Join-Path $RepoDir "daily_autopilot.py"
$ReplyScript = Join-Path $RepoDir "outreach\reply_notifier.py"
$LogsDir = Join-Path $RepoDir "logs"

$DailyTaskName = "LeadPipeline-DailyAutopilot"
$ReplyTaskName = "LeadPipeline-ReplyWatcher"

if ($Status) {
    Write-Host "`n=== LeadPipeline Windows Scheduled Tasks ===" -ForegroundColor Cyan
    foreach ($name in @($DailyTaskName, $ReplyTaskName)) {
        $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($t) {
            $info = Get-ScheduledTaskInfo -TaskName $name
            Write-Host "Task:       $name" -ForegroundColor Green
            Write-Host "  State:    $($t.State)"
            Write-Host "  Exec:     $($t.Actions.Execute)"
            Write-Host "  LastRun:  $($info.LastRunTime) (Result: $($info.LastTaskResult))"
            Write-Host "  NextRun:  $($info.NextRunTime)"
            Write-Host "  CatchUp:  StartWhenAvailable = $($t.Settings.StartWhenAvailable)"
        } else {
            Write-Host "Task:       $name -> NOT INSTALLED" -ForegroundColor Yellow
        }
    }
    exit 0
}

if ($Uninstall) {
    foreach ($name in @($DailyTaskName, $ReplyTaskName)) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "Removed scheduled task: $name" -ForegroundColor Yellow
        }
    }
    Write-Host "All LeadPipeline scheduled tasks removed." -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $PythonWExe)) {
    throw "Virtualenv pythonw.exe not found at $PythonWExe"
}
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

# Execute directly via pythonw.exe (Windows GUI subsystem -> ZERO CMD / Windows Terminal popup ever!)
$DailyAction = New-ScheduledTaskAction `
    -Execute $PythonWExe `
    -Argument "`"$AutopilotScript`"" `
    -WorkingDirectory $RepoDir

$DailyTrigger = New-ScheduledTaskTrigger -Daily -At "03:00PM"

$DailySettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 5) `
    -MultipleInstances IgnoreNew `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 15)

Register-ScheduledTask `
    -TaskName $DailyTaskName `
    -Action $DailyAction `
    -Trigger $DailyTrigger `
    -Settings $DailySettings `
    -Description "LeadPipeline Tier-1 Daily Autopilot (100% silent via pythonw.exe): checks replies, refills Tier-1 leads headlessly, audits with Gemini + 4-Hydrogen demos, sends up to 30 Gmail-safe emails. Catches up automatically when PC comes online." `
    -Force | Out-Null

# Task 2: Lightweight 30-Minute Reply Watcher (100% silent via pythonw.exe -> logs/reply_watcher.log)
$ReplyAction = New-ScheduledTaskAction `
    -Execute $PythonWExe `
    -Argument "`"$ReplyScript`"" `
    -WorkingDirectory $RepoDir

$ReplyTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At ((Get-Date).AddMinutes(2)) `
    -RepetitionInterval (New-TimeSpan -Minutes 30)

$ReplySettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $ReplyTaskName `
    -Action $ReplyAction `
    -Trigger $ReplyTrigger `
    -Settings $ReplySettings `
    -Description "LeadPipeline 30-min IMAP Reply Watcher: detects real prospect replies, halts follow-ups, drafts closing reply with gemini-4-hydrogen, and alerts ALERT_EMAIL immediately." `
    -Force | Out-Null

Write-Host "`n✅ SUCCESS! Both Windows Scheduled Tasks installed:" -ForegroundColor Green
Write-Host "  1. $DailyTaskName -> Daily 3:00 PM (+ automatic catch-up when PC boots/reconnects)"
Write-Host "  2. $ReplyTaskName -> Every 30 minutes (instant alerts + AI closing drafts to ALERT_EMAIL)"
Write-Host "`nCheck status anytime with: .\setup_scheduler.ps1 -Status" -ForegroundColor Cyan
