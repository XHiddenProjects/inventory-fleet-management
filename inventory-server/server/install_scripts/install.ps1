# Inventory agent installer for Windows.
#
# NOTE: this script has NOT been executed/tested in a real Windows
# environment (none was available while building this) - it was written
# carefully and reviewed for correctness, but treat it as a first draft to
# verify on an actual Windows machine before relying on it. Everything else
# in this project (server, Linux/macOS installers, all collectors) HAS been
# tested for real; this file is the one exception, flagged deliberately
# rather than silently.
#
# Usage:
#   iwr -useb <server>/agent/install.ps1 | iex
#   Install-InventoryAgent -Server 'http://server:8787' -EnrollmentKey 'enr_...'

function Install-InventoryAgent {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Server,
        [Parameter(Mandatory = $true)][string]$EnrollmentKey,
        [int]$IntervalSeconds = 60,
        [string]$InstallDir = "$env:ProgramFiles\InventoryAgent"
    )

    $ErrorActionPreference = "Stop"

    $currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error "Run this from an elevated (Administrator) PowerShell prompt."
        return
    }

    $py = $null
    foreach ($candidate in @("python", "python3")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { $py = $cmd.Source; break }
    }
    if (-not $py) {
        Write-Error "Python 3 is required but was not found on PATH. Install it from https://www.python.org/downloads/windows/ (check 'Add python.exe to PATH') and re-run."
        return
    }

    Write-Host "Installing inventory agent to $InstallDir ..."
    New-Item -ItemType Directory -Force -Path "$InstallDir\collectors" | Out-Null

    $files = @(
        "inventory_agent.py",
        "collectors\__init__.py",
        "collectors\system.py",
        "collectors\software.py",
        "collectors\processes.py",
        "collectors\network.py",
        "collectors\identity.py",
        "collectors\services.py"
    )
    foreach ($f in $files) {
        $remotePath = $f -replace '\\', '/'
        $url = "$Server/agent/files/$remotePath"
        $dest = Join-Path $InstallDir $f
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $dest
    }

    Write-Host "Fetching psutil wheel from the inventory server (no internet needed) ..."
    $wheel = "psutil-7.2.2-cp37-abi3-win_amd64.whl"
    $wheelDest = Join-Path $InstallDir $wheel
    Invoke-WebRequest -UseBasicParsing -Uri "$Server/agent/files/vendor/wheels/$wheel" -OutFile $wheelDest

    Write-Host "Installing Python dependencies (psutil) ..."
    & $py -m pip install --quiet --no-index $wheelDest
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install of $wheel failed (exit code $LASTEXITCODE). Check the Python installation and try again."
        return
    }

    $taskName = "InventoryAgent"
    $scriptPath = Join-Path $InstallDir "inventory_agent.py"
    $arguments = "`"$scriptPath`" --server $Server --enrollment-key $EnrollmentKey --interval $IntervalSeconds"

    $action = New-ScheduledTaskAction -Execute $py -Argument $arguments
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
}
