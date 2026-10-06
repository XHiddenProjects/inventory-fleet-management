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

    $pythonCandidates = @()
    $launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($launcher) {
        $resolvedPython = & $launcher.Source -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $resolvedPython) {
            $pythonCandidates += $resolvedPython.Trim()
        }
    }
    foreach ($candidate in @("python.exe", "python3.exe")) {
        Get-Command $candidate -All -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandType -eq "Application" } |
            ForEach-Object { $pythonCandidates += $_.Source }
    }

    $profileRoot = [System.IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\') + '\'
    $py = $null
    foreach ($candidate in ($pythonCandidates | Select-Object -Unique)) {
        if ($candidate -match "(?i)\\Microsoft\\WindowsApps\\" -or
            $candidate.StartsWith($profileRoot, [System.StringComparison]::OrdinalIgnoreCase)) { continue }
        if (-not (Test-Path $candidate)) { continue }
        & $candidate -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $candidate; break }
    }
    if (-not $py) {
        Write-Error "A machine-wide Python 3 interpreter was not found. The WindowsApps alias and Python installed inside a user profile cannot be used by the SYSTEM scheduled task. Install Python for all users (for example, under C:\Program Files\Python312) and re-run."
        return
    }
    Write-Host "Using Python: $py"

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
    & $py -m pip install --quiet --no-index --upgrade --target $InstallDir $wheelDest
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install of $wheel failed (exit code $LASTEXITCODE). Check the Python installation and try again."
        return
    }

    $taskName = "InventoryAgent"
    $scriptPath = Join-Path $InstallDir "inventory_agent.py"
    $statePath = Join-Path $env:ProgramData "inventory-agent\state.json"
    $logPath = Join-Path $InstallDir "agent.log"

    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

    Write-Host "Testing enrollment and first inventory check-in ..."
    $agentArgs = @($scriptPath, "--server", $Server, "--enrollment-key", $EnrollmentKey,
        "--interval", "$IntervalSeconds", "--once")
    & $py @agentArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Initial enrollment or check-in failed. Resolve the error above before the scheduled task is created."
        return
    }
    if (-not (Test-Path $statePath)) {
        Write-Error "The agent did not create its state file at $statePath. Enrollment did not complete."
        return
    }

    $arguments = "`"$scriptPath`" --server `"$Server`" --interval $IntervalSeconds --log-file `"$logPath`""

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
    Write-Host "Agent output log: $logPath"
}
