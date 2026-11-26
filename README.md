# SolidCADWorks

Design in SolidWorks by talking to Claude. SolidCADWorks connects Claude to the SolidWorks running on your
Windows PC, so you can ask for a part, an assembly, a drawing or a stress check and watch it being built.

It bundles a local MCP connector (about 80 SolidWorks tools) with twelve skills that teach Claude standard
mechanical-engineering workflows, plus an interactive studio for adjusting dimensions with sliders.

## What you can ask for

| Area | Example |
| :--- | :--- |
| Parts | "Model a flanged mounting bracket with four M6 counterbored holes." |
| Interactive studio | "Open the studio so I can adjust the dimensions with sliders." |
| Standard hardware | "Create a DIN 933 M12 hex bolt with a 45 mm shaft." |
| Assemblies | "Insert bracket.sldprt and pin.sldprt and mate the pin into the hole." |
| Drawings | "Make an A3 drawing with front, top and isometric views and a BOM, then export a PDF." |
| Simulation | "Fix the base, apply 2,000 N to the top face and report the factor of safety." |
| Manufacturability | "Check this part for CNC milling: pocket radii for a 6 mm tool and wall thickness." |
| Inspection | "What are the volume, surface area and bounding box of the active part?" |

## Requirements

- Windows with SolidWorks installed (developed against SolidWorks 2025). Simulation features need the
  SolidWorks Simulation add-in; Toolbox features need Toolbox.
- Python 3.10 or newer ([python.org](https://www.python.org/downloads/), tick *Add python.exe to PATH*),
  or [uv](https://docs.astral.sh/uv/getting-started/installation/). The connector is a Python program.
  With uv on your PATH it runs the exact versions pinned in `uv.lock`. Without uv, the first start
  creates a private environment from your Python and installs the dependencies into it, which takes
  about a minute. If the first connection reports that it is still installing, reconnect a minute
  later (`/mcp` in Claude Code).
- A Claude surface that runs local connectors: **Cowork in the Claude desktop app** (sessions that run on
  your computer) or Claude Code. The skills also load in ordinary chat, but chat cannot start a local
  connector, so it cannot drive SolidWorks.

## Install

1. In the Claude desktop app open **Customize > Plugins**, choose **Add > Add marketplace**, enter
   `Antniom/Solidworks-MCP`, then add *SolidCADWorks* from the list. (Or download the repository as a zip
   and use **Add > Upload plugin**.)
2. Start SolidWorks.
3. Start a Cowork task and ask for something from the table above. The connector appears on the plugin's
   **Connectors** tab as *Runs in each session*.

## Skills

| Skill | What it covers |
| :--- | :--- |
| `solidworks-project` | Whole-product projects: interviews you, collects your files, writes `AGENT_CONTEXT.md` and works module by module against it as a tracklist |
| `solidworks-modeling` | Sketches, features, sheet metal, global variables, selection and verification |
| `solidworks-interactive` | The studio with live dimension sliders |
| `solidworks-assemblies` | Components, mates, kinematics, positional states, interference checks |
| `solidworks-drawings` | Views, sections, model dimensions, BOM tables, PDF/DXF/DWG export |
| `solidworks-simulation` | Static FEA: materials, fixtures, loads, mesh, results |
| `solidworks-dfm` | Design-for-manufacturing rules for CNC, sheet metal, molding and 3D printing |
| `solidworks-purchased-parts` | Default for fasteners, bearings and other catalogue parts: models only their holes and seats, and lists the parts to download from TraceParts or the maker |
| `solidworks-din-parts` | DIN/ISO fasteners modeled from dimension tables, when you ask for the part itself |
| `solidworks-toolbox` | SolidWorks Toolbox hardware |
| `solidworks-diagnostics` | Dialogs, rebuild errors, checkpoints, transactions and recovery |
| `solidworks-welcome-demo` | First-run check and a small demo part after installing |

Trigger test sets for each skill's description are in `evals/skill-triggers/` (regenerate with
`python scripts/make_trigger_evals.py`). They use the format of the skill-creator description optimiser.

## Interactive studio

`show_interactive_studio` shows the active model's driving dimensions as sliders next to a viewport image.
Moving a slider rebuilds the model in SolidWorks; if the rebuild fails, the slider returns to the last good
value. You can also type an exact value, switch between standard views, reset, or send a summary of the
changes back to the chat.

The studio is an [MCP App](https://github.com/modelcontextprotocol/ext-apps): one self-contained HTML
view (`ui://solidworks/studio.html`) that clients supporting MCP Apps (Claude, Codex, Antigravity and
others) show inline. It has no network access of its own and talks to the connector only through the
client. In clients without MCP Apps, ask Claude to change dimensions with `set_dimension` instead.

## What runs on your computer, and your data

Everything this plugin does happens on your PC:

- **The connector** is the Python package in `solidworks_mcp/`, started by `scripts/start-server.cmd` from
  the plugin folder. That script uses `uv run --locked` when uv is installed. Otherwise it uses the first
  Python 3.10+ it finds (`python`, `py -3`, `python3`) to build a private environment in the plugin's
  data folder (`%USERPROFILE%\.claude\plugins\data\...`, or `%LOCALAPPDATA%\SolidCADWorks` outside Claude
  Code) and installs the dependencies from `pyproject.toml` into it. On first start these packages are
  downloaded from PyPI; the install log is `install.log` in that folder. The connector also starts the
  bundled .NET runner (`MecAgent.SolidWorks.Runner.exe`) the first time a runner-backed tool is used, and
  controls SolidWorks through its COM automation interface.
- **Files**: it reads and writes CAD files, exports and screenshots only in locations you name, limited to
  your user profile, the working folder and the temp folder. Logs and checkpoint copies go to
  `%LOCALAPPDATA%\solidworks-mcp`.
- **Network**: model geometry, images and tool results go only to Claude, as part of your conversation.
  The connector has no telemetry and no self-update. Its one network request of its own is the
  **`report_issue`** tool: when a tool misbehaves, or a needed tool is missing, Claude files a structured
  bug report (tool name, arguments, output, what was expected). The connector also files a short report
  by itself when a tool fails for a server-side reason (internal error, file load/save, missing
  template, export, timeout), once per kind of failure per session. Either kind is sent to the
  developer's Firebase database (project `solidcadworks-reports`, Google Cloud, EU region). This happens
  automatically, without asking each time. Before sending, folder paths are cut to `<path>\` (file names
  stay), and your Windows user name and any e-mail addresses are masked. Each report carries a random
  installation id, the connector, SolidWorks, Windows and Python versions, and nothing else about you or
  your PC. The database accepts new reports only; nobody can read them back through it. A copy of every
  report is kept in `%LOCALAPPDATA%\solidworks-mcp\reports`. To keep reports on your PC only, set the
  environment variable `SW_MCP_REPORTING=0`.
- **Running code**: one tool, `execute_code`, can run custom Python or C# with your Windows permissions. It
  is locked by default. Claude must ask you first and can only unlock it for the current session after you
  agree. Setting the environment variable
  `SW_MCP_ALLOW_EXEC=1` unlocks it permanently; leave it unset unless you want that.
- **Stopping stray processes**: `supervisor_clean_processes` can end orphaned SolidWorks processes, and only
  when called with explicit confirmation.

## The .NET runner

Some tool modes run in a separate .NET process that ships with the plugin in
`solidworks_mcp/bin/mecagent_runner`: fast model context and parametric feature trees (`get_feature_tree`),
topology paths (`inspect_topology`, `select` by path), the seven-view overview capture, and C# in
`execute_code`. It starts on first use and stops with the connector. Because of these bundled binaries the
plugin is distributed from this repository rather than Anthropic's public directory, which does not accept
files of that size.

## Troubleshooting

- **The connector does not start**: check that `python --version` (3.10 or newer) or `uv --version` works
  in a new terminal. On the very first start, wait a minute for the dependency install and reconnect;
  its progress is in `install.log` in the plugin's data folder. Then restart the desktop app or run
  `/mcp` in Claude Code.
- **"SolidWorks is blocked by a modal dialog"**: ask Claude to inspect and dismiss the dialog, or close it
  yourself.
- **A tool timed out**: ask Claude to check the model before retrying; the operation may still have finished.

## Development

```powershell
git clone https://github.com/Antniom/Solidworks-MCP.git
cd Solidworks-MCP
uv sync --locked --extra dev
uv run pytest
```

Tool schemas are in `solidworks_mcp/tool_schemas.py` and the dispatch table in `solidworks_mcp/server.py`.
The studio view is `solidworks_mcp/ui/studio.html` (plain HTML and JavaScript, no build step) and its
state lives in `solidworks_mcp/ui/studio.py`.
To use the same server with other MCP clients, see [docs/other-clients.md](docs/other-clients.md).

## License

MIT for this project's own code, see [LICENSE](LICENSE). The files in `solidworks_mcp/bin/mecagent_runner` are third-party binaries (the MecAgent runner, Microsoft
Roslyn, Newtonsoft.Json and SolidWorks interop assemblies) and remain under their owners' licences.

SolidWorks is a trademark of Dassault Systèmes SolidWorks Corporation. This is an independent project and is
not affiliated with or endorsed by Dassault Systèmes.
