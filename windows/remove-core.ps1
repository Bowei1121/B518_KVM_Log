$ErrorActionPreference = 'Stop'
if (Get-ScheduledTask -TaskName 'B518 Core' -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName 'B518 Core' -Confirm:$false
}
