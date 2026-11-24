# Using the server with other MCP clients

`python -m solidworks_mcp.install` registers the server with every supported client it finds and copies the skills where the client supports them. Existing config files are backed up (`*.solidworks-mcp.bak`) and merged; a file that cannot be parsed is left untouched.

| Client | Config written | Notes |
| :--- | :--- | :--- |
| **Claude Desktop** | `%APPDATA%\Claude\claude_desktop_config.json` | Shows the studio inline (MCP Apps) |
| **Google Antigravity / Gemini** | `~/.gemini/config/mcp_config.json` | Plus a plugin bundle in `~/.gemini/config/plugins/solidworks` |
| **OpenAI Codex** | `~/.codex/config.toml` (`[mcp_servers.solidworks]`) | Manifest in `.codex-plugin/` |
| **Cursor** | `~/.cursor/mcp.json` | Manifest in `.cursor-plugin/` |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json` | |
| **VS Code / GitHub Copilot** | `%APPDATA%\Code\User\mcp.json` (`servers`) | |

Claude Code is not on this list: install the SolidCADWorks plugin from the marketplace instead. It brings the
server and the skills. If an older version of this installer registered Claude Code, remove the `solidworks`
entry from `~/.claude.json` (`claude mcp remove solidworks -s user`) and the `solidworks-*` folders from
`~/.claude/skills`, so that each tool and skill appears only once.

Use `--client <name>` to configure one client, `--dry-run` to preview, and `--no-install-plugin` to skip the Antigravity bundle.

## Setup

```powershell
git clone https://github.com/Antniom/Solidworks-MCP.git
cd Solidworks-MCP
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m solidworks_mcp.install
```

The installer is never run by the Claude plugin; it only runs when you start it yourself. It edits the MCP
configuration files listed above and nothing else. Restart the client afterwards.

