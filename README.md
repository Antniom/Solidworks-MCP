# SolidWorks CAD for Claude

Design in SolidWorks by talking to Claude. This plugin connects Claude to the SolidWorks running on your
Windows PC, so you can ask for a part, an assembly, a drawing or a stress check and watch it being built.

It bundles a local MCP connector (about 100 SolidWorks tools) with nine skills that teach Claude standard
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
- [uv](https://docs.astral.sh/uv/getting-started/installation/) on your PATH. The connector is a Python
  program; uv installs the exact dependency versions pinned in `uv.lock` the first time it starts.
- A Claude surface that runs local connectors: **Cowork in the Claude desktop app** (sessions that run on
  your computer) or Claude Code. The skills also load in ordinary chat, but chat cannot start a local
  connector, so it cannot drive SolidWorks.

## Install

1. In the Claude desktop app open **Customize > Plugins**, find *SolidWorks CAD* (or use
   **Add > Add marketplace** with `Antniom/Solidworks-MCP`) and select **Add**.
2. Start SolidWorks.
3. Start a Cowork task and ask for something from the table above. The connector appears on the plugin's
   **Connectors** tab as *Runs in each session*.

## Skills

| Skill | What it covers |
| :--- | :--- |
| `solidworks-modeling` | Sketches, features, sheet metal, global variables, selection and verification |
| `solidworks-interactive` | The studio with live dimension sliders |
| `solidworks-assemblies` | Components, mates, kinematics, positional states, interference checks |
| `solidworks-drawings` | Views, sections, model dimensions, BOM tables, PDF/DXF/DWG export |
| `solidworks-simulation` | Static FEA: materials, fixtures, loads, mesh, results |
| `solidworks-dfm` | Design-for-manufacturing rules for CNC, sheet metal, molding and 3D printing |
| `solidworks-din-parts` | DIN/ISO fasteners modeled from dimension tables |
| `solidworks-toolbox` | SolidWorks Toolbox hardware |
| `solidworks-diagnostics` | Dialogs, rebuild errors, checkpoints, transactions and recovery |

## Interactive studio

`show_interactive_studio` shows the active model's driving dimensions as sliders next to a viewport image.
Moving a slider rebuilds the model in SolidWorks; if the rebuild fails, the slider returns to the last good
value. The studio is described with [A2UI](https://github.com/a2ui-project/a2ui) 1.0 messages (basic
catalog) and drawn by the A2UI web renderer, so it has no hand-written layout.

Where the app supports MCP Apps the studio appears inline and talks to the connector through Claude.
Elsewhere Claude gives you a link that opens it in your browser from a small server on `127.0.0.1`; that
server accepts only requests carrying a per-session token, so other web pages cannot use it.

## What runs on your computer, and your data

Everything this plugin does happens on your PC:

- **The connector** is the Python package in `solidworks_mcp/`, started by `uv run --locked` from the plugin
  folder. On first start uv downloads the pinned Python packages in `uv.lock` from PyPI. The connector then
  controls SolidWorks through its COM automation interface.
- **Files**: it reads and writes CAD files, exports and screenshots only in locations you name, limited to
  your user profile, the working folder and the temp folder. Logs and checkpoint copies go to
  `%LOCALAPPDATA%\solidworks-mcp`.
- **Network**: the connector sends nothing to any server. It makes no network requests of its own and has
  no telemetry or self-update. Model geometry, images and tool results go only to Claude, as part of your
  conversation.
- **Running code**: three tools can run custom code with your Windows permissions (`execute_python`,
  `sw_execute_csharp`, `sw_execute_macro`). They are locked by default. Claude must ask you first and can
  only unlock them for the current session after you agree. Setting the environment variable
  `SW_MCP_ALLOW_EXEC=1` unlocks them permanently; leave it unset unless you want that.
- **Stopping stray processes**: `supervisor_clean_processes` can end orphaned SolidWorks processes, and only
  when called with explicit confirmation.

## Optional: fast inspection tools

The `sw_*` tools (fast model context, topology paths, C# execution) use the .NET runner from the separate
MecAgent desktop app. It is not included here. If MecAgent is installed, or `MECAGENT_RUNNER_PATH` points at
`MecAgent.SolidWorks.Runner.exe`, those tools work; otherwise they report that they are unavailable and
everything else works normally.

## Troubleshooting

- **The connector does not start**: check that `uv --version` works in a new terminal, then restart the
  desktop app.
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
The studio page is `solidworks_mcp/ui/host/studio.js`, bundled (unminified) into
`solidworks_mcp/ui/static/studio.bundle.js` with `npm install && npm run build` in `solidworks_mcp/ui/host`.
To use the same server with other MCP clients, see [docs/other-clients.md](docs/other-clients.md).

## License

MIT, see [LICENSE](LICENSE). The bundled studio renderer includes A2UI (Apache-2.0) and its dependencies;
their notices are in `solidworks_mcp/ui/static/studio.bundle.js.LEGAL.txt`.

SolidWorks is a trademark of Dassault Systèmes SolidWorks Corporation. This is an independent project and is
not affiliated with or endorsed by Dassault Systèmes.
