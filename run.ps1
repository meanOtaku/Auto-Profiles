<#
    Single-command runner for the Face Profile System on native Windows 10/11 x64
    (M17). This is a native PowerShell port of run.sh -- it does not shell out to
    WSL, Git Bash, or any POSIX layer, and it never uses an elevated/admin prompt.

    Unlike run.sh (whose default config/default.yaml is fully hardware-disabled
    and mock-backed), this script's default is config/windows-full.yaml -- the
    practical, real-camera/real-model/real-settings Windows config documented in
    docs/RUNNING_ON_WINDOWS.md. Running `.\run.ps1` with no arguments executes
    `preflight`: it verifies the pinned model files and probes
    settings capabilities, but deliberately does not open the webcam. `serve` and
    `detect` use the real camera and settings paths enabled by that config.
    For a hardware-free, non-mutating check instead -- e.g. to validate install/
    import/config/CLI paths before ever touching a camera -- pass
    `-Config config\windows-safe.yaml` explicitly, or set
    $env:FACE_PROFILE_CONFIG. This asymmetry with run.sh's safe-by-default
    behavior is deliberate and is called out again at runtime below, not a
    hidden surprise.

    uv is required and is never installed silently. If it is missing, this
    script prints the official Windows installer command and exits. It never
    downloads or executes remote installer code itself.

    Usage:
      .\run.ps1                                        # non-mutating preflight (config/windows-full.yaml)
      .\run.ps1 -Config config\windows-safe.yaml check  # hardware-free, non-mutating check
      .\run.ps1 detect --debug-output out.png           # any face-profile subcommand

      .\run.ps1 -Help
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    [Console]::Error.WriteLine("error: $Message")
    exit 1
}

# Repo-root-relative, independent of the caller's current directory -- the
# PowerShell equivalent of run.sh's `readlink -f "${BASH_SOURCE[0]}"`.
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

Set-Location -LiteralPath $RepoDir

function Show-Usage {
    @'
Usage: .\run.ps1 [-Config PATH] [face-profile args...]

With no arguments, runs the non-mutating `preflight` command against
config/windows-full.yaml: verifies pinned model bytes and probes real Windows
settings capabilities without opening the webcam. `serve` and `detect` open the
camera. Pass -Config config\windows-safe.yaml for a fully hardware-free check.

Options (handled by this wrapper, not forwarded to face-profile):
  -Config PATH   Use PATH instead of config/windows-full.yaml.

  -Help          Show this help and exit.

Anything else is forwarded to `face-profile` unchanged, for example:
  .\run.ps1 detect --debug-output out.png
  .\run.ps1 -Config config\windows-safe.yaml check
  .\run.ps1 serve
  .\run.ps1 profile list

Environment:
  FACE_PROFILE_CONFIG   Default config path if -Config is not given.

See docs\RUNNING_ON_WINDOWS.md for camera/driver/index troubleshooting,
pycaw/Core Audio, WMI brightness, and firewall/loopback notes.
'@
}

$ConfigPath = $null
$PassArgs = [System.Collections.Generic.List[string]]::new()

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
        [void]$PassArgs.Add($arg)
        $i++
    }
}

$configWasExplicit = [bool]$ConfigPath
if (-not $ConfigPath) {
    if ($env:FACE_PROFILE_CONFIG) {
        $ConfigPath = $env:FACE_PROFILE_CONFIG
    }
    else {
        $ConfigPath = 'config/windows-full.yaml'
    }
}

if (-not $configWasExplicit -and -not $env:FACE_PROFILE_CONFIG) {
    Write-Warning ("No -Config given: using config\windows-full.yaml, which verifies real models " + `
        "and probes real settings. `serve`/`detect` will open the webcam. For a fully hardware-free check, run: " + `
        ".\run.ps1 -Config config\windows-safe.yaml")
}

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    Fail ("config file not found: $ConfigPath (default is config/windows-full.yaml; " + `
        "config/windows-safe.yaml is the hardware-free check; see docs\RUNNING_ON_WINDOWS.md)")
}

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    $installCmd = 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    [Console]::Error.WriteLine("uv was not found on PATH.")
    [Console]::Error.WriteLine("Install it explicitly with the official command, then open a new PowerShell session:")
    [Console]::Error.WriteLine("  $installCmd")
    Fail "uv is required; this launcher never downloads or executes installer code for you."
}

& uv sync --locked --all-groups
if ($LASTEXITCODE -ne 0) {
    Fail "uv sync --locked --all-groups failed (exit code $LASTEXITCODE)"
}

if ($PassArgs.Count -eq 0) {
    [void]$PassArgs.Add('preflight')
}

& uv run face-profile --config $ConfigPath @PassArgs
exit $LASTEXITCODE
