#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$NodeExe = Join-Path $Root "tools\node\node.exe"
$GeminiEntry = Join-Path $Root "tools\gemini-cli\node_modules\@google\gemini-cli\bundle\gemini.js"

if (-not (Test-Path $NodeExe)) {
    throw "Missing Node executable: $NodeExe"
}
if (-not (Test-Path $GeminiEntry)) {
    throw "Missing Gemini CLI entry: $GeminiEntry"
}

$nodeVersion = & $NodeExe --version
if ($LASTEXITCODE -ne 0) { throw "node --version failed" }

$geminiVersion = & $NodeExe $GeminiEntry --version
if ($LASTEXITCODE -ne 0) { throw "gemini --version failed" }

Write-Host "Node: $nodeVersion"
Write-Host "Gemini CLI: $geminiVersion"
Write-Host "Gemini entry: $GeminiEntry"
