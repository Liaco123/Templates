[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipEnv,
    [switch]$SkipTemplates,
    [switch]$CopyTemplates,
    [string]$TemplateDest,
    [ValidateSet("auto", "clang", "gcc", "msvc")]
    [string]$Compiler = "auto",
    [ValidateSet("auto", "libc++", "libstdc++", "msvc")]
    [string]$Stdlib = "auto",
    [ValidateSet("auto", "system", "lld", "bfd", "mold", "msvc")]
    [string]$Linker = "auto",
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
$UvVersion = "0.11.28"
$ProjectRoot = $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

function Find-Uv {
    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        (Join-Path $HOME ".local\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "uv\bin\uv.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

$uv = Find-Uv
if (-not $uv) {
    Write-Host "[bootstrap] uv not found; installing pinned version $UvVersion."
    $env:UV_NO_MODIFY_PATH = "1"
    Invoke-RestMethod "https://astral.sh/uv/$UvVersion/install.ps1" | Invoke-Expression
    $uv = Find-Uv
}

if (-not $uv) {
    throw "uv is still unavailable. See https://docs.astral.sh/uv/getting-started/installation/."
}

Write-Host "[bootstrap] Syncing the project-local Python toolchain."
& $uv sync --locked
if ($LASTEXITCODE -ne 0) {
    throw "uv sync --locked failed."
}

$setupArgs = @()
if ($CheckOnly) { $setupArgs += "--check-only" }
if ($SkipEnv) { $setupArgs += "--skip-env" }
if ($SkipTemplates) { $setupArgs += "--skip-templates" }
if ($CopyTemplates) { $setupArgs += "--copy-templates" }
if ($TemplateDest) { $setupArgs += @("--template-dest", $TemplateDest) }
$setupArgs += @("--compiler", $Compiler, "--stdlib", $Stdlib, "--linker", $Linker)
if ($NonInteractive) { $setupArgs += "--non-interactive" }

Write-Host "[bootstrap] Initializing the development environment and Conan templates."
& $uv run --locked python setup.py @setupArgs
exit $LASTEXITCODE
