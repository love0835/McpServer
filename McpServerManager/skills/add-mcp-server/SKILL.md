# Add MCP Server Skill

Use this checklist when adding a new server under `E:\McpServer\servers`.

1. Create a folder named with a stable kebab-case id.
2. Add a valid `mcpserver.json` that follows `McpServerManager\rules\mcpserver.schema.json`.
3. Use relative paths or Manager variables (`${MCP_ROOT}`, `${SERVER_DIR}`) for `cwd`, log files, and local scripts whenever possible.
4. Pick a unique port for HTTP or SSE transports.
5. Add a health check. Use `type: "mcp"` for streamable HTTP MCP endpoints.
6. If any tool can receive large text or launch slow work, implement the large-text job pattern from `McpServerManager\rules\server-contract.md`:
   - Do not put prompts or file bodies in command-line arguments.
   - Use stdin, attachments, or files under `${SERVER_DIR}\state`.
   - Provide attach, submit, get, cancel, and list job tools.
   - Make output reads cursor-based and bounded.
   - Redact prompts from logs and audit lines.
7. Add server-specific env values for state, prompt limits, chunk limits, and concurrency where applicable.
8. Keep `autostart` false until the server has been manually started and verified.
9. Start the server from McpServerManager and confirm status, PID, port, health, and log output.
10. Run a large-text smoke test before enabling or publishing an agent bridge:
    - Attach at least 250 KB of text.
    - Submit as a background job.
    - Confirm submit returns immediately.
    - Confirm `get_*_job` returns within its bounded wait window.
    - Cancel the job if it is still running.
11. Only enable autostart after manual verification.
