# Installs the inventory agent EXE (no Python required on this machine).
# Run from an elevated PowerShell prompt, with inventory-agent.exe sitting
# next to this script (or pass -ExePath).
#
# Usage:
#   .\install-exe.ps1 -Server 'http://inventory.example.com:8787' -EnrollmentKey 'enr_...'

function Install-InventoryAgentExe {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Server,
        [Parameter(Mandatory = $true)][string]$EnrollmentKey,
        [int]$IntervalSeconds = 60,
        [string]$InstallDir = "$env:ProgramFiles\InventoryAgent",
        [string]$ExePath = ".\inventory-agent.exe"
    )

    $ErrorActionPreference = "Stop"

    $currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error "Run this from an elevated (Administrator) PowerShell prompt."
        return
    }

    if (-not (Test-Path $ExePath)) {
        Write-Error "Could not find $ExePath. Build it first with build.ps1, or pass -ExePath to its location."
        return
    }

    Write-Host "Installing inventory agent to $InstallDir ..."
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    $dest = Join-Path $InstallDir "inventory-agent.exe"
    Copy-Item -Path $ExePath -Destination $dest -Force

    $taskName = "InventoryAgent"
    $arguments = "--server $Server --enrollment-key $EnrollmentKey --interval $IntervalSeconds"

    $action = New-ScheduledTaskAction -Execute $dest -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Days 0)

    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Description "Inventory & Fleet Management agent" | Out-Null

    Start-ScheduledTask -TaskName $taskName

    Write-Host "Installed and started scheduled task '$taskName'."
    Write-Host "Check status with: Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo"
    Write-Host "First enroll attempt happens within a few seconds - watch the server's agent list."
}

Install-InventoryAgentExe @args
