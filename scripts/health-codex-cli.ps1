#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$ToolVersionsPath = Join-Path $Root ".tool-versions"
$NodeExe = Join-Path $Root "tools\node\node.exe"
$CodexJs = Join-Path $Root "tools\codex-cli\node_modules\@openai\codex\bin\codex.js"

function Read-ToolVersions {
    $map = @{}
    Get-Content $ToolVersionsPath | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        $parts = $line.Split("=", 2)
        $map[$parts[0].Trim()] = $parts[1].Trim()
    }
    return $map
}

$versions = Read-ToolVersions
$NodeVersion = $versions["NODE_VERSION"]
$CodexVersion = $versions["CODEX_VERSION"]

if (-not (Test-Path $NodeExe)) { throw "Missing Node executable: $NodeExe" }
if (-not (Test-Path $CodexJs)) { throw "Missing Codex entrypoint: $CodexJs" }

$nodeOut = (& $NodeExe --version).Trim()
if ($nodeOut -ne "v$NodeVersion") {
    throw "Node version mismatch. Expected v$NodeVersion, got $nodeOut"
}

$codexOut = (& $NodeExe $CodexJs --version).Trim()
if ($codexOut -notmatch [regex]::Escape($CodexVersion)) {
    throw "Codex version mismatch. Expected $CodexVersion, got $codexOut"
}

Write-Host "OK Node $nodeOut"
Write-Host "OK $codexOut"
