# MCP Server Manager Contract

Each managed server lives under `E:\McpServer\servers\<server-name>` and must
include `mcpserver.json` in its root.

## Required manifest fields

- `name`: Unique kebab-case id.
- `displayName`: Human-friendly UI label.
- `version`: Server package version.
- `transport`: `stdio`, `streamable-http`, or `sse`.
- `command`: Executable used to start the server.

## Recommended fields

- `args`: Command arguments as an array.
- `cwd`: Working directory. `.` means the manifest folder.
- `env`: Server-specific environment values.
- `host` and `port`: Required for HTTP/SSE servers.
- `healthcheck`: Prefer `mcp` for streamable HTTP MCP servers, `tcp` for simple port checks.
- `logging.stdout` and `logging.stderr`: Relative paths are resolved from the server folder.
- `autostart`: Whether Manager starts this server when Manager launches.

## Operational rules

- Bind local development servers to `127.0.0.1` unless there is a documented reason.
- Do not store secrets directly in `mcpserver.json`; use environment variables or a future Manager secret store.
- Keep server logs inside the server folder or Manager logs folder.
- A server must tolerate being started and stopped by process control.
- A server should expose a cheap health check.
- Ports must be unique across managed HTTP/SSE servers.

## Conservative bridge rules

- Agent bridges should have isolated config, logs, allowed directories, and timeouts.
- Bridges that can call Codex must include recursion protection.
- Bridges that can write files should start with a narrow allowed directory list.
