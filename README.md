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

- `servers\claude-bridge`: Streamable HTTP MCP bridge for Claude Code CLI. Short requests can use `ask_claude`; long requests should use the async job tools.
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

## Claude Bridge Job API

Use the job API for long Claude Code requests so MCP clients do not wait on one
long `tools/call`.

```text
bridge_attach_prompt(content, attachment_id?, append?) -> attachment_id
submit_claude_job(prompt?, prompt_attachment?, working_dir?, resume_last?, timeout_secs?) -> job_id
get_claude_job(job_id, wait_ms?, stdout_cursor?, stderr_cursor?, max_bytes?) -> status and chunks
cancel_claude_job(job_id)
list_claude_jobs(status?)
```

Bridge state is stored under the Claude bridge state directory, not inside user
repositories.
