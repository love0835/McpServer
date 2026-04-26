# McpServer

Local Windows MCP server workspace.

## Layout

```text
E:\McpServer\
  servers\
    claude-bridge\
    codex-mcp-bridge\
  McpServerManager\
```

## Components

- `servers\claude-bridge`: Streamable HTTP MCP bridge for Claude Code CLI.
- `servers\codex-mcp-bridge`: Conservative streamable HTTP MCP bridge for Codex CLI. It uses a project-local portable Node/Codex install and `codex exec` with read-only sandboxing.
- `McpServerManager`: WPF manager app that scans `servers\**\mcpserver.json`, starts/stops servers, checks health, tails logs, and supports Traditional Chinese/English UI.
- `scripts`: Setup and health scripts for portable tools.

## Manager

Executable:

```text
McpServerManager\McpServerManager.exe
```

Source project:

```text
McpServerManager\McpServerManager.csproj
```

Publish command:

```powershell
dotnet publish E:\McpServer\McpServerManager\McpServerManager.csproj -c Release -r win-x64 --self-contained false /p:PublishSingleFile=true -o E:\McpServer\McpServerManager\dist
```

## Server Contract

Every managed server should include:

```text
mcpserver.json
```

Rules and schema live in:

```text
McpServerManager\rules\
McpServerManager\skills\add-mcp-server\
```

## Codex CLI Repair

The WindowsApps Codex alias can fail from subprocesses with `Access is denied`.
Do not point the bridge at WindowsApps or a user-specific Node path. Run the
portable setup script instead:

```powershell
.\scripts\setup-codex-cli.ps1
```

This downloads the pinned official Node zip to `tools\node` and installs the
pinned `@openai/codex` package to `tools\codex-cli`. The `tools` directory is a
local generated artifact and is not committed.

Health check:

```powershell
.\scripts\health-codex-cli.ps1
```

Managed server manifests may use:

```text
${MCP_ROOT}
${MANAGER_DIR}
${SERVER_ROOT}
${SERVER_DIR}
```
