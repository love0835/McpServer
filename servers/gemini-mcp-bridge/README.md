# Gemini MCP Bridge

This bridge is intentionally conservative.

- `gemini_version` is available for diagnostics.
- `ask_gemini` is disabled by default. Enable it with `GEMINI_BRIDGE_ENABLE_ASK=1` only after review.
- Large or long-running requests should use the job API:
  1. `gemini_attach_prompt(...)`
  2. `submit_gemini_job(...)`
  3. `get_gemini_job(...)`
  4. optionally `cancel_gemini_job(...)`
- The default allowed directory is the MCP workspace root.
- When enabled, Gemini runs in headless mode with `--approval-mode plan`.
- Prompts are passed to Gemini through stdin, not command-line arguments. This avoids Windows command-line length failures for large text.

The bridge expects the portable Gemini CLI install:

```text
GEMINI_NODE_EXE=${MCP_ROOT}\tools\node\node.exe
GEMINI_JS_ENTRY=${MCP_ROOT}\tools\gemini-cli\node_modules\@google\gemini-cli\bundle\gemini.js
```

To install or repair Gemini CLI:

```powershell
.\scripts\setup-gemini-cli.ps1
```

Gemini CLI authentication is separate from the bridge. Run the CLI interactively
once if Google login or API key setup is required:

```powershell
E:\McpServer\tools\node\node.exe E:\McpServer\tools\gemini-cli\node_modules\@google\gemini-cli\bundle\gemini.js
```
