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
- `servers\codex-mcp-bridge`: Conservative streamable HTTP MCP bridge scaffold for Codex CLI. `ask_codex` is disabled by default.
- `McpServerManager`: WPF manager app that scans `servers\**\mcpserver.json`, starts/stops servers, checks health, tails logs, and supports Traditional Chinese/English UI.

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
