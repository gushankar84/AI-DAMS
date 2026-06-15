# Registers the DAM reliability watchdog as a Windows Scheduled Task that starts at logon and
# keeps running, so the ingest worker + model server self-heal across reboots and sessions —
# not just while a Claude/terminal session is open.
#
#   Install:    powershell -ExecutionPolicy Bypass -File scripts\install_watchdog_task.ps1
#   Remove:     Unregister-ScheduledTask -TaskName "DAM-Watchdog" -Confirm:$false
#   Inspect:    Get-ScheduledTask -TaskName "DAM-Watchdog"; Get-Content .data\watchdog.log -Tail 20

$py  = "E:\dam-platform\services\ai-worker\.venv\Scripts\python.exe"
$wd  = "E:\dam-platform\scripts\watchdog.py"
$cwd = "E:\dam-platform\services\ai-worker"

$action  = New-ScheduledTaskAction -Execute $py -Argument "`"$wd`"" -WorkingDirectory $cwd
$trigger = New-ScheduledTaskTrigger -AtLogOn
# Keep it alive indefinitely; restart the task itself if it ever exits.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask -TaskName "DAM-Watchdog" -Action $action -Trigger $trigger `
    -Settings $settings -RunLevel Highest -Force -Description `
    "Keeps the DAM ingest worker + model server alive and relieves GPU VRAM pressure."

Write-Host "Installed scheduled task 'DAM-Watchdog' (runs at logon). Starting it now..."
Start-ScheduledTask -TaskName "DAM-Watchdog"
Write-Host "Done. Tail the log:  Get-Content E:\dam-platform\.data\watchdog.log -Tail 20 -Wait"
