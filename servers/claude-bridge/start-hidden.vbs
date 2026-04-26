' Hidden launcher for Claude MCP Bridge
Option Explicit
Dim WshShell, cmd
Set WshShell = CreateObject("WScript.Shell")
cmd = """C:\Users\love0\AppData\Local\Programs\Python\Python312\pythonw.exe""" & " " & """E:\McpServer\servers\claude-bridge\server.py"""
WshShell.Run cmd, 0, False
