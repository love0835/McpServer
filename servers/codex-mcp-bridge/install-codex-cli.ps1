$ErrorActionPreference = "Stop"

$target = "E:\McpServer\tools\npm-global"
$package = "@openai/codex"

function Find-NodeRoot {
    $wingetRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe"
    if (Test-Path $wingetRoot) {
        $node = Get-ChildItem $wingetRoot -Recurse -Filter node.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "node-v.*-win-x64\\node.exe$" } |
            Select-Object -First 1
        if ($node) {
            return Split-Path $node.FullName -Parent
        }
    }

    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "\\WindowsApps\\") {
        return Split-Path $cmd.Source -Parent
    }

    throw "Could not find a usable Node.js installation. Install Node.js LTS with winget or npm first."
}

$nodeRoot = Find-NodeRoot
$npm = Join-Path $nodeRoot "npm.cmd"
$node = Join-Path $nodeRoot "node.exe"

if (-not (Test-Path $npm)) {
    throw "npm.cmd not found beside Node.js: $npm"
}

New-Item -ItemType Directory -Force -Path $target | Out-Null
& $npm install -g $package --prefix $target
if ($LASTEXITCODE -ne 0) {
    throw "npm install failed with exit code $LASTEXITCODE"
}

Copy-Item $node (Join-Path $target "node.exe") -Force
& (Join-Path $target "codex.cmd") --version
