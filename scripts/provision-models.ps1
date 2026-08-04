<#
    Idempotently download and integrity-verify the two models config/windows-full.yaml
    (and config/jetson-full.yaml) pin: the YuNet face detector and the SFace face-
    recognition embedding model, both from OpenCV Zoo
    (https://github.com/opencv/opencv_zoo). This is a native PowerShell port of
    scripts/provision-models.sh for Windows (M17) -- it pins the exact same URLs and
    SHA-256 values as that script; see docs/MODELS.md for full provenance/license.

    Fails closed: a missing, partial, or hash-mismatched download is never installed
    for use. If a file already exists at the destination with the correct hash, it is
    left untouched and nothing is re-downloaded (idempotent). If a file already exists
    with an INCORRECT hash, this script refuses to silently overwrite it -- remove it
    yourself first if you want it re-fetched.

    Unlike the bash script (whose DEST_DIR is relative to the caller's current
    directory), this script resolves its own location and treats a relative
    -DestDir as repo-root-relative, so it behaves the same regardless of which
    directory you run it from -- matching run.ps1/run-continuous.ps1's convention.

    Downloads go to a temporary file in the destination directory first and are
    only moved (atomically, same-volume rename) into place after Get-FileHash
    confirms an exact, case-insensitive SHA-256 match -- a mismatched or partial
    download never becomes the file OpenCV/ONNX Runtime would load.

    Usage:
      .\scripts\provision-models.ps1                    # downloads into .\models\
      .\scripts\provision-models.ps1 -DestDir data\models  # repo-root-relative destination
#>

param(
    [Parameter()]
    [string]$DestDir = 'models'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    [Console]::Error.WriteLine("error: $Message")
    exit 1
}

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

$ScriptPath = $MyInvocation.MyCommand.Path
if (-not $ScriptPath) {
    Fail "could not resolve this script's own path (unsupported invocation method)"
}
$RepoDir = Split-Path -Parent (Split-Path -Parent $ScriptPath)
$PyprojectPath = Join-Path $RepoDir 'pyproject.toml'

if (-not (Test-Path -LiteralPath $PyprojectPath -PathType Leaf)) {
    Fail "could not locate the face-profile-system repository root next to this script (expected $PyprojectPath)"
}
$pyprojectText = Get-Content -LiteralPath $PyprojectPath -Raw
if ($pyprojectText -notmatch '(?m)^name = "face-profile-system"') {
    Fail "could not locate the face-profile-system repository root next to this script (expected $PyprojectPath)"
}

if ([System.IO.Path]::IsPathRooted($DestDir)) {
    $ResolvedDestDir = $DestDir
}
else {
    $ResolvedDestDir = Join-Path $RepoDir $DestDir
}

New-Item -ItemType Directory -Path $ResolvedDestDir -Force | Out-Null

# Same two files/URLs/hashes as scripts/provision-models.sh; keep these in sync
# with that script and docs/MODELS.md if either ever changes.
$Models = @(
    [ordered]@{
        Name   = 'face_detection_yunet_2023mar.onnx'
        Url    = 'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx'
        Sha256 = '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'
    },
    [ordered]@{
        Name   = 'face_recognition_sface_2021dec.onnx'
        Url    = 'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx'
        Sha256 = '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79'
    }
)

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Test-LfsPointer {
    # A real ONNX file is a binary protobuf; a Git LFS pointer is always this
    # exact ASCII text preamble regardless of the real object's size -- same
    # check as scripts/provision-models.sh's looks_like_lfs_pointer().
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $buffer = New-Object byte[] 32
        $read = $stream.Read($buffer, 0, 32)
        if ($read -le 0) {
            return $false
        }
        $text = [System.Text.Encoding]::ASCII.GetString($buffer, 0, $read)
        return $text.StartsWith('version https://git-lfs')
    }
    finally {
        $stream.Dispose()
    }
}

function Invoke-ModelDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$OutFile
    )
    $maxAttempts = 3
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
            return
        }
        catch {
            if (Test-Path -LiteralPath $OutFile) {
                Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
            }
            if ($attempt -eq $maxAttempts) {
                # Only a coarse, controlled message -- never the raw exception object,
                # which can embed host-specific proxy/network details.
                Fail "download failed for $Url after $maxAttempts attempts"
            }
        }
    }
}

function Install-Model {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256
    )

    $dest = Join-Path $ResolvedDestDir $Name
    Write-Step "Checking $Name"

    if (Test-Path -LiteralPath $dest -PathType Leaf) {
        $actual = Get-Sha256Hex -Path $dest
        if ($actual -ieq $ExpectedSha256) {
            Write-Host "$Name already present and verified (sha256 $actual); skipping download."
            return
        }
        Fail ("$dest already exists but its sha256 ($actual) does not match the pinned value " + `
            "($ExpectedSha256). Refusing to overwrite an unexpected file -- remove it yourself " + `
            "first if you want it re-downloaded.")
    }

    Write-Step "Downloading $Name"
    $tempFile = Join-Path $ResolvedDestDir ("." + $Name + "." + [System.IO.Path]::GetRandomFileName() + ".tmp")

    Invoke-ModelDownload -Url $Url -OutFile $tempFile

    if (Test-LfsPointer -Path $tempFile) {
        Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
        Fail ("$Name downloaded as a Git LFS pointer, not real model bytes -- $Url is not " + `
            "resolving to the actual object. This is a fail-closed refusal, not a corrupted-" + `
            "download false pass.")
    }

    $actual = Get-Sha256Hex -Path $tempFile
    if (-not ($actual -ieq $ExpectedSha256)) {
        Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
        Fail "sha256 mismatch for $Name`: expected $ExpectedSha256, got $actual. Refusing to install an unverified model file."
    }

    # Same-directory rename is an atomic replace on NTFS -- the destination
    # never observably contains a partially written file.
    Move-Item -LiteralPath $tempFile -Destination $dest -Force
    Write-Host "$Name downloaded and verified (sha256 $actual)."
}

foreach ($model in $Models) {
    Install-Model -Name $model.Name -Url $model.Url -ExpectedSha256 $model.Sha256
}

Write-Step "All models provisioned and verified in $ResolvedDestDir"
