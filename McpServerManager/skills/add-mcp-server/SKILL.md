# Add MCP Server Skill

Use this checklist when adding a new server under `E:\McpServer\servers`.

1. Create a folder named with a stable kebab-case id.
2. Add a valid `mcpserver.json` that follows `McpServerManager\rules\mcpserver.schema.json`.
3. Use relative paths or Manager variables (`${MCP_ROOT}`, `${SERVER_DIR}`) for `cwd`, log files, and local scripts whenever possible.
4. Pick a unique port for HTTP or SSE transports.
5. Add a health check. Use `type: "mcp"` for streamable HTTP MCP endpoints.
6. Keep `autostart` false until the server has been manually started and verified.
7. Start the server from McpServerManager and confirm status, PID, port, and log output.
8. Only enable autostart after manual verification.
