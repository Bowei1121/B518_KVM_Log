$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
$core = Join-Path $dir 'Core.exe'
if (!(Test-Path $core)) { throw "Core.exe not found: $core" }
$config = Get-Content (Join-Path $dir 'core-scheduler.json') -Raw | ConvertFrom-Json
if ($config.interval_minutes -lt 1 -or $config.red_threshold_seconds -le 0) { throw 'Invalid scheduler settings' }
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $core -Argument 'start' -WorkingDirectory $dir
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
# A second trigger runs indefinitely; the CLI start command pings before starting Core.
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $config.interval_minutes)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'B518 Core' -Action $action -Trigger @($logon, $repeat) -Settings $settings -Principal $principal -Force | Out-Null
$task = Get-ScheduledTask -TaskName 'B518 Core'
$task | Format-List *
$task.Triggers | Format-List *
$task.Actions | Format-List *
$task.Settings | Format-List *
Write-Host "Task B518 Core: user=$($task.Principal.UserId) runLevel=$($task.Principal.RunLevel)"
Write-Host "Repeat every $($task.Triggers[1].Repetition.Interval), duration '$($task.Triggers[1].Repetition.Duration)' (empty = no end)"
Write-Host "ExecutionTimeLimit=$($task.Settings.ExecutionTimeLimit) MultipleInstances=$($task.Settings.MultipleInstances) DisallowStartIfOnBatteries=$($task.Settings.DisallowStartIfOnBatteries) StopIfGoingOnBatteries=$($task.Settings.StopIfGoingOnBatteries)"
Write-Host "Action: $($task.Actions[0].Execute) $($task.Actions[0].Arguments) in $($task.Actions[0].WorkingDirectory)"
Write-Host "Red threshold for the bridge light: $($config.red_threshold_seconds) s (core-scheduler.json)"
