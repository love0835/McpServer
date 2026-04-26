# Codex MCP Bridge

This bridge is intentionally conservative.

- `codex_version` is available for diagnostics.
- `ask_codex` is enabled through `CODEX_BRIDGE_ENABLE_ASK=1`.
- `MCP_CALL_DEPTH` prevents nested Codex calls.
- The default allowed directory is `E:\McpServer`.
- When enabled, `ask_codex` uses `codex exec` with read-only sandboxing.

The preferred local CLI path is:

```text
E:\McpServer\tools\npm-global\codex.cmd
```

The WindowsApps app execution alias may return `Access is denied`; avoid using
that path from subprocess-based bridges.

To install or repair the local npm-based Codex CLI:

```powershell
.\install-codex-cli.ps1
```
