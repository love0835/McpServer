# Codex MCP Bridge

This bridge is intentionally conservative.

- `codex_version` is available for diagnostics.
- `ask_codex` is disabled unless `CODEX_BRIDGE_ENABLE_ASK=1`.
- `MCP_CALL_DEPTH` prevents nested Codex calls.
- The default allowed directory is `E:\McpServer`.

Before enabling `ask_codex`, confirm the local Codex CLI can run from a normal
subprocess. The WindowsApps app execution alias may return `Access is denied`.
