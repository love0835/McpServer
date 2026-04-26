#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$ToolVersionsPath = Join-Path $Root ".tool-versions"
$ToolsDir = Join-Path $Root "tools"
$NodeDir = Join-Path $ToolsDir "node"
$GeminiDir = Join-Path $ToolsDir "gemini-cli"
$VersionFile = Join-Path $GeminiDir ".version"
$LogFile = Join-Path $ToolsDir "setup-gemini.log"

function Log($Message) {
    New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $Message
}

function Read-ToolVersions {
    if (-not (Test-Path $ToolVersionsPath)) {
        throw "Missing .tool-versions at $ToolVersionsPath"
    }

    $map = @{}
    Get-Content $ToolVersionsPath | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        $parts = $line.Split("=", 2)
        if ($parts.Count -ne 2) { throw "Invalid .tool-versions line: $line" }
        $map[$parts[0].Trim()] = $parts[1].Trim()
    }
    return $map
}

$versions = Read-ToolVersions
$GeminiVersion = $versions["GEMINI_VERSION"]
if (-not $GeminiVersion) {
    throw "Missing GEMINI_VERSION in .tool-versions"
}

$ExpectedVersionText = "gemini=$GeminiVersion"
if ((Test-Path $VersionFile) -and ((Get-Content -Raw $VersionFile).Trim() -eq $ExpectedVersionText)) {
    Log "Gemini CLI already installed: $ExpectedVersionText"
    & (Join-Path $ScriptDir "health-gemini-cli.ps1")
    exit $LASTEXITCODE
}

if (-not (Test-Path (Join-Path $NodeDir "npm.cmd"))) {
    throw "Missing portable Node/npm at $NodeDir. Run scripts/setup-codex-cli.ps1 first."
}

try {
    Log "Installing @google/gemini-cli@$GeminiVersion"
    if (Test-Path $GeminiDir) { Remove-Item -Recurse -Force $GeminiDir }
    New-Item -ItemType Directory -Force -Path $GeminiDir | Out-Null
    & (Join-Path $NodeDir "npm.cmd") install --prefix $GeminiDir "@google/gemini-cli@$GeminiVersion"
    if ($LASTEXITCODE -ne 0) { throw "npm install failed with exit code $LASTEXITCODE" }

    Set-Content -Path $VersionFile -Value $ExpectedVersionText -Encoding ASCII
    Log "Gemini install complete"
    & (Join-Path $ScriptDir "health-gemini-cli.ps1")
    exit $LASTEXITCODE
}
catch {
    Log ("Gemini install failed: " + $_.Exception.Message)
    if (Test-Path $GeminiDir) { Remove-Item -Recurse -Force $GeminiDir -ErrorAction SilentlyContinue }
    throw
}
