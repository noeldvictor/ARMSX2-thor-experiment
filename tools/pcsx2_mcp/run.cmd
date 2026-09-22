@echo off
rem MCP launcher for .mcp.json: creates the venv on first use, then runs the server on stdio.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv >nul 2>&1 || py -3 -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
)
".venv\Scripts\python.exe" server.py %*
