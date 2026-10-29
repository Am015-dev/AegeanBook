# Registers ONE daily Windows Task Scheduler job that runs award_daily.bat (the AEGEAN award miles collection).
# Run it yourself, once, when you are sure AEGEAN's bot check has expired and `python award_collector.py login` has been done:
#     powershell -ExecutionPolicy Bypass -File schedule_award_daily.ps1
# Remove it again with:
#     Unregister-ScheduledTask -TaskName "AEGEAN award miles" -Confirm:$false
# The job runs only while you are logged in (the collector opens a visible Chrome window, so you can solve a human check if one appears),
# once a day, and never twice at the same time. It stops by itself at the first bot check or hang.
$bat = Join-Path $PSScriptRoot "award_daily.bat"
$action = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 8:30am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName "AEGEAN award miles" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Collects AEGEAN award miles for the four legs and publishes data/daily_award.csv" | Out-Null
Write-Host "Registered 'AEGEAN award miles': daily at 08:30. Remove with: Unregister-ScheduledTask -TaskName 'AEGEAN award miles' -Confirm:`$false"
