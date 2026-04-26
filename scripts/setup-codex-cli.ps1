#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$ToolVersionsPath = Join-Path $Root ".tool-versions"
$ToolsDir = Join-Path $Root "tools"
$NodeDir = Join-Path $ToolsDir "node"
$CodexDir = Join-Path $ToolsDir "codex-cli"
$VersionFile = Join-Path $ToolsDir ".version"
$LogFile = Join-Path $ToolsDir "setup.log"

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
$NodeVersion = $versions["NODE_VERSION"]
$CodexVersion = $versions["CODEX_VERSION"]
$ExpectedVersionText = "node=$NodeVersion;codex=$CodexVersion"

if ((Test-Path $VersionFile) -and ((Get-Content -Raw $VersionFile).Trim() -eq $ExpectedVersionText)) {
    Log "Tools already installed: $ExpectedVersionText"
    & (Join-Path $ScriptDir "health-codex-cli.ps1")
    exit $LASTEXITCODE
}

Log "Installing tools: $ExpectedVersionText"
New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null

$NodeZipName = "node-v$NodeVersion-win-x64.zip"
$NodeBaseUrl = "https://nodejs.org/dist/v$NodeVersion"
$ZipUrl = "$NodeBaseUrl/$NodeZipName"
$ShasumsUrl = "$NodeBaseUrl/SHASUMS256.txt"
$TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("mcpserver-node-" + [System.Guid]::NewGuid().ToString("N"))
$ZipPath = Join-Path $TempDir $NodeZipName

try {
    New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
    Log "Downloading $ZipUrl"
    Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing

    Log "Downloading $ShasumsUrl"
    $shasums = Invoke-WebRequest -Uri $ShasumsUrl -UseBasicParsing
    $expectedHash = (($shasums.Content -split "`n") | Where-Object { $_ -match [regex]::Escape($NodeZipName) } | Select-Object -First 1).Split(" ")[0]
    if (-not $expectedHash) {
        throw "Could not find hash for $NodeZipName"
    }

    $actualHash = (Get-FileHash -Algorithm SHA256 -Path $ZipPath).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash.ToLowerInvariant()) {
        throw "Node zip SHA256 mismatch. Expected $expectedHash, got $actualHash"
    }

    Log "Extracting Node"
    if (Test-Path $NodeDir) { Remove-Item -Recurse -Force $NodeDir }
    $ExtractDir = Join-Path $TempDir "extract"
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractDir -Force
    $ExtractedNodeRoot = Get-ChildItem $ExtractDir -Directory | Select-Object -First 1
    if (-not $ExtractedNodeRoot) { throw "Node archive did not contain a directory" }
    Move-Item -Path $ExtractedNodeRoot.FullName -Destination $NodeDir

    Log "Installing @openai/codex@$CodexVersion"
    if (Test-Path $CodexDir) { Remove-Item -Recurse -Force $CodexDir }
    New-Item -ItemType Directory -Force -Path $CodexDir | Out-Null
    & (Join-Path $NodeDir "npm.cmd") install --prefix $CodexDir "@openai/codex@$CodexVersion"
    if ($LASTEXITCODE -ne 0) { throw "npm install failed with exit code $LASTEXITCODE" }

    Set-Content -Path $VersionFile -Value $ExpectedVersionText -Encoding ASCII
    Log "Install complete"
    & (Join-Path $ScriptDir "health-codex-cli.ps1")
    exit $LASTEXITCODE
}
catch {
    Log ("Install failed: " + $_.Exception.Message)
    if (Test-Path $NodeDir) { Remove-Item -Recurse -Force $NodeDir -ErrorAction SilentlyContinue }
    if (Test-Path $CodexDir) { Remove-Item -Recurse -Force $CodexDir -ErrorAction SilentlyContinue }
    throw
}
finally {
    if (Test-Path $TempDir) { Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue }
}
