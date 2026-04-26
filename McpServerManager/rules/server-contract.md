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
- Paths may use `${MCP_ROOT}`, `${MANAGER_DIR}`, `${SERVER_ROOT}`, and `${SERVER_DIR}`.
- Generated tools belong under `${MCP_ROOT}\tools` and must not be committed.

## Large text and long-running calls

- Do not pass user prompts, file contents, transcripts, generated documents, or other large text through command-line arguments.
- Prefer stdin, temporary files under a bridge-owned state directory, or structured attachments for large payloads.
- Any tool that may call an LLM, agent CLI, compiler, browser, or slow external process should have a background job API:
  - `*_attach_prompt` or equivalent for large input chunks.
  - `submit_*_job` that returns a `job_id` immediately.
  - `get_*_job` with bounded `wait_ms`, cursors, and output chunk limits.
  - `cancel_*_job` for running jobs.
  - `list_*_jobs` for diagnostics.
- Synchronous tools should remain for short diagnostics only and must document the async alternative.
- Job state must live under `${SERVER_DIR}\state` or another Manager-resolved state directory and must be ignored by git.
- Output reads must be chunked and cursor-based. Do not return unbounded stdout, stderr, logs, or generated text in one MCP response.
- Log/audit command lines must redact or replace prompt payloads with markers such as `<stdin>` or `<attachment>`.

## Conservative bridge rules

- Agent bridges should have isolated config, logs, allowed directories, and timeouts.
- Bridges that can call Codex must include recursion protection.
- Bridges that can write files should start with a narrow allowed directory list.
