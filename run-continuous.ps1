<#
    Continuous launcher for the Face Profile System on native Windows 10/11 x64
    (M17): always runs the long-running `serve` API daemon, delegating to
    run.ps1 for every bit of uv bootstrap/sync/exec logic exactly like
    run-continuous.sh delegates to run.sh -- this script never duplicates that
    logic, it only adds what Windows genuinely needs on top of it.

    Windows has no systemd. run-continuous.sh's own docs/RUNBOOK.md entry says
    outright "No systemd unit is provided by this repository... wrap it in your
    own systemd service" and simply execs `run.sh ... serve` once, relying on an
    external supervisor for restart-on-crash. Windows has no equivalent
    lightweight convention available out of the box, so this script is instead a
    self-contained foreground restart loop: if `face-profile serve` exits with a
    non-zero code (a crash), it is restarted after a bounded, exponentially
    increasing delay (capped at 60s, reset once a session has stayed up longer
    than 30s); a clean exit (code 0) is not restarted. This is deliberately
    **not** a real Windows Service (no `New-Service`/`sc.exe`/NSSM-style
    integration, no auto-start-on-boot, no run-as-a-different-account) -- it is
    an honest foreground restart loop, not a fake service, and it provides no
    guarantee of surviving a console/terminal closing, logoff, or reboot. See
    docs\RUNNING_ON_WINDOWS.md for what this does and does not guarantee.

    Ctrl+C is not intercepted or trapped: Windows delivers it to the whole
    console process group (this script and the child `uv run` process
    together), so pressing it terminates both immediately and this loop never
    treats that as a crash to restart from -- there is no "restart after
    Ctrl+C" code path because control never returns to the loop body afterward.

    Usage:
      .\run-continuous.ps1                                   # serve config/continuous.yaml (loopback API only)
      .\run-continuous.ps1 -Config config\windows-full.yaml   # serve the real camera/model/settings config instead

      .\run-continuous.ps1 -Help
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    [Console]::Error.WriteLine("error: $Message")
    exit 1
}

$ScriptPath = $MyInvocation.MyCommand.Path
if (-not $ScriptPath) {
    Fail "could not resolve this script's own path (unsupported invocation method)"
}
$RepoDir = Split-Path -Parent $ScriptPath
$PyprojectPath = Join-Path $RepoDir 'pyproject.toml'

if (-not (Test-Path -LiteralPath $PyprojectPath -PathType Leaf)) {
    Fail "could not locate the face-profile-system repository root next to this script (expected $PyprojectPath)"
}
$pyprojectText = Get-Content -LiteralPath $PyprojectPath -Raw
if ($pyprojectText -notmatch '(?m)^name = "face-profile-system"') {
    Fail "could not locate the face-profile-system repository root next to this script (expected $PyprojectPath)"
}

$RunPs1Path = Join-Path $RepoDir 'run.ps1'

function Show-Usage {
    @'
Usage: .\run-continuous.ps1 [-Config PATH]

Always starts the long-running `face-profile serve` API daemon through
run.ps1/uv, in a bounded-delay foreground restart loop -- it never falls back
to any other face-profile subcommand.

With no arguments, serves the repository-shipped config/continuous.yaml:
loopback-only REST/WebSocket API enabled (no auth_token required on
loopback), camera/detection/database/recognition/enrollment/every other
sensitive capability disabled/mock -- unlike run.ps1's own default
(config/windows-full.yaml), this script's default deliberately stays safe,
since an unattended auto-restarting loop is a worse place for a surprising
real-hardware/real-settings default than a single foreground run.ps1 call.
Pass -Config config\windows-full.yaml explicitly for the real camera/model/
settings path.

Options:
  -Config PATH   Serve PATH instead of config/continuous.yaml.

  -Help          Show this help and exit.

Environment:
  FACE_PROFILE_CONFIG   Default config path if -Config is not given,
                         forwarded to run.ps1 unchanged.

See docs\RUNNING_ON_WINDOWS.md for exactly what this loop does and does not
guarantee (no real Windows Service, no auto-start-on-boot, no
survive-a-logoff guarantee).
'@
}

$ConfigPath = $null

$i = 0
while ($i -lt $args.Count) {
    $arg = $args[$i]
    if ($arg -eq '-Config' -or $arg -eq '--config') {
        if ($i + 1 -ge $args.Count) {
            Fail "-Config requires a path argument"
        }
        $ConfigPath = $args[$i + 1]
        $i += 2
    }
    elseif ($arg -like '--config=*') {
        $ConfigPath = $arg.Substring('--config='.Length)
        $i++
    }
    elseif ($arg -eq '-Help' -or $arg -eq '-h' -or $arg -eq '--help') {
        Show-Usage
        exit 0
    }
    else {
        Fail ("unrecognized argument: $arg (run-continuous.ps1 always runs 'serve' with no extra " + `
            "face-profile arguments; use -Config/-InstallUv/-Help, or run .\run.ps1 directly for " + `
            "other subcommands)")
    }
}

if (-not $ConfigPath) {
    if ($env:FACE_PROFILE_CONFIG) {
        $ConfigPath = $env:FACE_PROFILE_CONFIG
    }
    else {
        $ConfigPath = Join-Path $RepoDir 'config/continuous.yaml'
    }
}

$forwardArgs = [System.Collections.Generic.List[string]]::new()
[void]$forwardArgs.Add('-Config')
[void]$forwardArgs.Add($ConfigPath)
[void]$forwardArgs.Add('serve')

$MinDelaySeconds = 1
$MaxDelaySeconds = 60
$StableRunThresholdSeconds = 30
$delaySeconds = $MinDelaySeconds

while ($true) {
    $startedAt = Get-Date
    & $RunPs1Path @forwardArgs
    $exitCode = $LASTEXITCODE
    $ranForSeconds = ((Get-Date) - $startedAt).TotalSeconds

    if ($exitCode -eq 0) {
        Write-Host "face-profile serve exited cleanly (exit code 0); not restarting."
        exit 0
    }

    if ($ranForSeconds -ge $StableRunThresholdSeconds) {
        $delaySeconds = $MinDelaySeconds
    }

    Write-Warning ("face-profile serve exited with code $exitCode after " + `
        "$([math]::Round($ranForSeconds, 1))s; restarting in ${delaySeconds}s (Ctrl+C to stop)...")
    Start-Sleep -Seconds $delaySeconds
    $delaySeconds = [Math]::Min($delaySeconds * 2, $MaxDelaySeconds)
}
