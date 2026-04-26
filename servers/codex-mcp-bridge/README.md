# Codex MCP Bridge

This bridge is intentionally conservative.

- `codex_version` is available for diagnostics.
- `ask_codex` is disabled by default. Enable it with `CODEX_BRIDGE_ENABLE_ASK=1` only after review.
- `MCP_CALL_DEPTH` prevents nested Codex calls.
- The default allowed directory is the MCP workspace root.
- When enabled, `ask_codex` uses `codex exec` with read-only sandboxing.

The bridge does not use `codex.cmd` or WindowsApps. It expects:

```text
CODEX_NODE_EXE=${MCP_ROOT}\tools\node\node.exe
CODEX_JS_ENTRY=${MCP_ROOT}\tools\codex-cli\node_modules\@openai\codex\bin\codex.js
```

The WindowsApps app execution alias may return `Access is denied`; avoid using
that path from subprocess-based bridges.

To install or repair the local npm-based Codex CLI:

```powershell
.\install-codex-cli.ps1
```
