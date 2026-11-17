@echo off
rem SolidCADWorks MCP server launcher (Windows).
rem stdout is the MCP protocol channel: nothing in this script may print to it.
rem   1. uv on PATH  -> uv run --locked (fast, uses uv.lock)
rem   2. otherwise   -> any Python >= 3.10 (python, py -3, python3) builds a private venv once,
rem                     in CLAUDE_PLUGIN_DATA (kept across plugin updates) or %LOCALAPPDATA%\SolidCADWorks.
setlocal EnableExtensions
set "PYTHONUNBUFFERED=1"
set "PYTHONIOENCODING=utf-8"
for %%I in ("%~dp0..") do set "ROOT=%%~fI"

where uv >nul 2>nul || goto :no_uv
uv run --quiet --locked --project "%ROOT%" python -m solidworks_mcp.server
exit /b %ERRORLEVEL%

:no_uv
set "PYEXE="
for %%C in (python "py -3" python3) do (
  if not defined PYEXE (
    for /f "delims=" %%E in ('%%~C "%~dp0pyprobe.py" 2^>nul') do set "PYEXE=%%E"
  )
)
if not defined PYEXE (
  1>&2 echo SolidCADWorks: no Python 3.10 or newer was found, and uv is not installed.
  1>&2 echo Install Python 3.10+ from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^)
  1>&2 echo or uv from https://docs.astral.sh/uv/ , then reconnect the server with /mcp.
  exit /b 1
)

if defined CLAUDE_PLUGIN_DATA (set "DATA=%CLAUDE_PLUGIN_DATA%") else (set "DATA=%LOCALAPPDATA%\SolidCADWorks")
"%PYEXE%" "%~dp0bootstrap_env.py" ensure "%ROOT%" "%DATA%" 1>&2
if errorlevel 1 exit /b %ERRORLEVEL%

set "PYTHONPATH=%ROOT%"
"%DATA%\venv\Scripts\python.exe" -m solidworks_mcp.server
exit /b %ERRORLEVEL%
