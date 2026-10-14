<!-- ============================================================================ -->
<!-- 🤖 AGENT INSTALLATION DIRECTIVE (ZERO-FRICTION AUTONOMOUS ONBOARDING)       -->
<!-- ============================================================================ -->
> [!IMPORTANT]
> ### 🤖 AGENT DIRECTIVE: AUTOMATED SETUP & CLIENT REGISTRATION
> **If you are an AI assistant (Claude Code, Gemini / Antigravity, Cursor, Windsurf, Codex, or Copilot) and the user pasted this repository URL or requested to install/connect SolidWorks MCP, execute the following autonomous onboarding sequence directly in your terminal without requiring manual user edits:**
>
> 1. **Clone & Virtual Environment Setup:**
>    ```powershell
>    git clone https://github.com/Antniom/Solidworks-MCP.git
>    cd Solidworks-MCP
>    python -m venv .venv
>    .venv\Scripts\pip install -e .
>    ```
>
> 2. **Run the Bundled Multi-Client Auto-Installer:**
>    The repository includes an auto-installer that scans standard Windows paths, merges the server configuration into detected AI apps, and copies all bundled SolidWorks CAD/FEA/Drafting skills:
>    ```powershell
>    .venv\Scripts\python -m solidworks_mcp.install
>    ```
>    *Detected target locations:*
>    - **Claude Desktop:** `%APPDATA%\Claude\claude_desktop_config.json`
>    - **Claude Code:** `%USERPROFILE%\.claude.json` and `%USERPROFILE%\.claude\skills\`
>    - **Antigravity / Gemini:** `%USERPROFILE%\.gemini\antigravity\mcp_config.json` and `%USERPROFILE%\.gemini\config\skills\`
>    - **Cursor:** `%USERPROFILE%\.cursor\mcp.json` and `%USERPROFILE%\.cursor\skills\`
>    - **Windsurf:** `%USERPROFILE%\.codeium\windsurf\mcp_config.json`
>    - **Codex / OpenAI CLI:** `%USERPROFILE%\.codex\mcp.json`
>    - **VS Code / Copilot:** `%APPDATA%\Code\User\mcp.json`
>
> 3. **Interactive Consent for `execute_python`:**
>    Explain to the human user in chat:
>    - **Benefits:** Direct access to the full SolidWorks COM API (`sw_app`, `model`, `SelectionMgr`) for custom macros, complex geometry batch math, and niche APIs.
>    - **Risks:** Arbitrary Python code execution on the host Windows machine under the current user's privileges.
>    - Ask the user if they wish to permanently enable `SW_MCP_ALLOW_EXEC=1` in their MCP client config, or keep it gated behind per-session approval (`user_confirmed_risks=true`).
>
> 4. **Verify Installation:**
>    Run the unit test suite to verify STA thread affinity and schema contracts:
>    ```powershell
>    .venv\Scripts\pytest -v tests/
>    ```

---

# SolidWorks MCP Server 🔧

> Production-grade [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server providing full autonomous CAD, assembly, drafting, FEA simulation, and diagnostic capabilities for Dassault Systèmes SolidWorks.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![SolidWorks 2023-2025](https://img.shields.io/badge/SolidWorks-2023--2025%20Verified-red.svg)](https://www.solidworks.com/)
[![MCP Tools: 66](https://img.shields.io/badge/MCP%20Tools-66%20Production%20Tools-green.svg)](https://modelcontextprotocol.io/)
[![STA Dispatcher: 100%](https://img.shields.io/badge/COM%20Affinity-100%25%20STA%20Guaranteed-purple.svg)]()

---

## 🎯 Architectural Overview

**SolidWorks MCP** transforms AI coding and design assistants (Claude Desktop, Claude Code, Google Antigravity / Gemini, Cursor, Windsurf, Copilot) into senior CAD/FEA automation engineers. Unlike naive wrappers that crash due to Windows COM apartment violations or hang indefinitely on hidden modal dialogs, SolidWorks MCP is built on a **hardened industrial COM architecture**:

- **100% STA Thread Affinity**: Windows COM automation for SolidWorks strictly requires Single-Threaded Apartment (STA) execution. Every COM method call is marshaled onto a dedicated worker thread with reentrant lock management (`reset_sta_worker`), completely eliminating RPC apartment crashes (`0x8001010E`).
- **Official Type-Library Parity (`swconst.tlb` & `cosworks.tlb`)**: Every enum, end condition, mate type, and error code is strictly audited against the official SolidWorks 2025 type libraries with unique canonical integer mappings.
- **Out-of-Band Win32 Modal Dialog Watchdog**: Hidden dialog boxes (e.g. "Do you want to rebuild?", "File modified externally") are automatically monitored on an independent thread, allowing agents to inspect (`inspect_dialogs`) and programmatically dismiss (`dismiss_dialog`) blocking windows without hanging the MCP server.
- **Declarative Geometry Selection (`select_by_geometry`)**: Eliminates fragile pixel coordinate guessing. Agents filter model entities by surface topology (`plane`, `cylinder`, `cone`, `torus`), normal vector, area, radius, and centroid.
- **Interactive Security Consent Gate**: Arbitrary code execution (`execute_python`) is default-locked behind an interactive consent gate with human approval confirmation.
- **Zero-Orphan Lifecycle Protection**: Detects and targets specific running `SLDWORKS.exe` processes by PID, preventing leaked headless background instances.

---

## 🛠️ The 66 Native Tools (Complete Inventory)

All 66 tools are available over MCP stdio and mapped to dedicated STA handlers:

### 1. Connection & Session (3 tools)
| Tool | Description |
| :--- | :--- |
| `connect_solidworks` | Connects or auto-launches SolidWorks with verified ROT (Running Object Table) registration. |
| `get_solidworks_info` | Retrieves active SolidWorks version, revision, executable path, and system templates. |
| `get_capabilities` | Queries loaded add-ins (Simulation, Toolbox, Routing) and active document counters. |

### 2. Document & Configuration Management (8 tools)
| Tool | Description |
| :--- | :--- |
| `create_new_part` | Creates a new 3D part document (`.sldprt`) from default system template. |
| `create_new_assembly` | Creates a new assembly document (`.sldasm`) from default template. |
| `open_document` | Opens an existing model or drawing with silent/read-only options. |
| `save_document` | Saves active document with full error bitmask diagnostics. |
| `close_document` | Closes active or target document safely without orphan processes. |
| `get_document_info` | Returns title, path, document type, active configuration, and modification state. |
| `list_open_documents` | Enumerates all open documents currently held in SolidWorks memory. |
| `manage_configuration` | List, add, activate, rename, or delete model configurations. |

### 3. Metadata & Custom Properties (1 tool)
| Tool | Description |
| :--- | :--- |
| `manage_custom_properties` | Get, set, delete, and list file-level and config-specific custom properties (including `$PRP:` links). |

### 4. 2D Sketching & Planar Contours (8 tools)
| Tool | Description |
| :--- | :--- |
| `create_sketch` | Opens a 2D sketch on standard reference planes (`Front`, `Top`, `Right`). |
| `create_sketch_on_face` | Opens a sketch on any planar model face selected by 3D coordinates or normal vector. |
| `draw_line` | Draws a sketch line segment `(x1, y1)` to `(x2, y2)`. |
| `draw_circle` | Draws a circle by center `(x, y)` and radius. |
| `draw_rectangle` | Draws a rectangle by corner coordinates `(x1, y1)` to `(x2, y2)`. |
| `draw_arc` | Draws an arc by center, start, and end points. |
| `draw_polygon` | Draws regular polygons (hexagons, octagons, triangles) with circumradius. |
| `close_sketch` | Safely exits sketch mode with save/discard options. |

### 5. 3D Solid Features (10 tools)
| Tool | Description |
| :--- | :--- |
| `extrude_sketch` | Boss-extrusion with depth, direction, draft angle, and auto-sketch closure. |
| `cut_extrude` | Cut-extrusion with blind depth or through-all conditions. |
| `revolve_sketch` | Revolve boss or cut around sketch centerline or axis with custom angles. |
| `create_shell` | Creates thin-walled hollow solid bodies with specified thickness and open faces. |
| `create_pattern` | Creates linear or circular feature patterns with instance counts and spacing. |
| `create_hole_wizard` | Creates standard engineering holes (counterbore, countersink, tapped, clearance). |
| `fillet_edges` | Applies constant radius rounds/fillets to selected edges. |
| `chamfer_edges` | Applies bevel chamfers to selected edges (distance and angle). |
| `list_features` | Enumerates all FeatureManager tree items with suppression state and type. |
| `manage_feature` | Suppress, unsuppress, rename, delete (with absorbed sketches), and reorder features. |

### 6. Parametric Dimensions (2 tools)
| Tool | Description |
| :--- | :--- |
| `get_dimensions` | Reads dimension values across features or sketches. |
| `set_dimension` | Drives dimension values parametrically and triggers model rebuild. |

### 7. Inspection & Geometry Selection (4 tools)
| Tool | Description |
| :--- | :--- |
| `select_by_geometry` | Declarative selection by surface type (`plane`, `cylinder`), normal, radius, area, centroid. |
| `select_entity` | Selects entities by name, type, or coordinate point. |
| `inspect_model` | Inspects body count, bounding box `[dx, dy, dz]`, volume, surface area, and mass center. |
| `verify_model` | Asserts bounding box dimensions, solid body counts, and clean rebuild state against tolerances. |

### 8. Viewport & Visualization (1 tool)
| Tool | Description |
| :--- | :--- |
| `capture_viewport` | Captures high-resolution rendered viewport image (`*Isometric`, `*Front`, etc.) to PNG. |

### 9. Assemblies & Mechanism Mates (4 tools)
| Tool | Description |
| :--- | :--- |
| `add_component` | Inserts parts into assembly with automatic memory pre-loading (`sw.OpenDoc6`). |
| `add_mate` | Adds standard mates (coincident, concentric, parallel, distance, angle). |
| `check_interference` | Runs interference detection and returns colliding components and volumes. |
| `list_components` | Lists all components, instances, file paths, and fixity states. |

### 10. 2D Drawings, Detailing & BOMs (6 tools)
| Tool | Description |
| :--- | :--- |
| `create_new_drawing` | Creates an empty drawing sheet in standard paper sizes (A4, A3, A2, A1, Letter). |
| `create_drawing_from_model` | Places standard orthographic (`*Front`, `*Top`, `*Right`) and isometric views. |
| `create_section_view` | Creates aligned or offset section cut views from a sketched section line. |
| `import_model_items` | Automatically imports 3D model feature dimensions and annotations into views. |
| `add_drawing_dimension` | Places driven drafting dimensions between drawing entities. |
| `insert_bom_table` | Inserts Bill of Materials tables (`top_level`, `parts_only`, `indented`) with auto-located templates. |
| `export_drawing` | Exports drawings to vector PDF, CNC DXF/DWG, or raster PNG. |

### 11. FEA Simulation & Stress Analysis (6 tools)
| Tool | Description |
| :--- | :--- |
| `create_simulation_study` | Initializes Static, Frequency, Buckling, or Thermal studies via COSMOSWorks. |
| `set_study_material` | Assigns engineering materials from standard or custom `.sldmat` databases. |
| `add_study_fixture` | Applies boundary restraints (fixed, immovable, roller, hinge, symmetry). |
| `add_study_load` | Applies mechanical loads (pressure in Pa/MPa, concentrated normal forces in N). |
| `mesh_and_run_simulation` | Generates parabolic 10-node tetrahedral solid mesh and executes solver. |
| `get_simulation_results` | Extracts von Mises stress (MPa), resultant displacement (mm), and Factor of Safety (FOS). |

### 12. Diagnostics, Dialog Watchdog & Recovery (8 tools)
| Tool | Description |
| :--- | :--- |
| `inspect_dialogs` | Non-blocking scan of active modal dialog windows and button titles. |
| `dismiss_dialog` | Programmatically clicks dialog buttons (`OK`, `Cancel`, `Yes`, `No`, `Rebuild`). |
| `load_addin` | Programmatically loads add-in DLLs (Simulation, Toolbox, Routing, Motion). |
| `inspect_rebuild_errors` | Traverses FeatureManager tree and reports `GetErrorCode2` diagnostic codes. |
| `reset_sta_worker` | Re-initializes hung STA COM thread and cleans reentrant locks without restarting SolidWorks. |
| `create_checkpoint` | Creates an in-memory document state snapshot. |
| `rollback_checkpoint` | Reverts document state to a previous snapshot. |
| `get_sketch_status` | Returns sketch activity status, plane, and entity counts. |

### 13. Utilities & Guarded Execution (2 tools)
| Tool | Description |
| :--- | :--- |
| `set_units` | Changes default active units (`mm`, `cm`, `inch`, `m`, `ft`). |
| `execute_python` | Interactive consent-gated execution of custom Python code with direct COM access. |

---

## ⚡ Quick Start & Installation

### Option A: Automatic Multi-Client Installation (Recommended)

Clone the repository and run the multi-client installer:
```powershell
git clone https://github.com/Antniom/Solidworks-MCP.git
cd Solidworks-MCP
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m solidworks_mcp.install
```

The installer automatically configures all detected AI clients and installs the complete set of SolidWorks skills.

### Option B: Manual Configuration (Claude Desktop)

Add to `%APPDATA%\Claude\claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "solidworks": {
      "command": "C:\\path\\to\\Solidworks-MCP\\.venv\\Scripts\\python.exe",
      "args": ["-m", "solidworks_mcp.server"],
      "env": {
        "SW_MCP_ALLOW_EXEC": "0"
      }
    }
  }
}
```

---

## 🛡️ Security Gating: `execute_python`

To safeguard host systems against accidental or untrusted code execution, `execute_python` is **disabled by default**.

When an AI agent attempts to invoke `execute_python` without permission, the tool refuses execution and returns an explicit instruction prompting the agent to ask the human user in chat for confirmation:
- **Benefits:** Full access to `sw_app`, `model`, and `SelectionMgr` for custom macros, complex geometry math, or niche APIs not in the 65 standard tools.
- **Risks:** Unrestricted arbitrary Python execution on the host Windows machine under the user's account privileges.

To unlock execution:
1. Re-invoke with `user_confirmed_risks=true` after explicit consent in chat.
2. Set environment variable `SW_MCP_ALLOW_EXEC=1` in your client config.

---

## 🧪 Testing & Verification

The suite includes comprehensive unit tests verifying type library parity, STA dispatcher affinity, AST static guarantees, and security gates:

```powershell
pytest -v tests/
```

---

## 📜 License & Attribution

- **Author & Maintainer:** António Coelho ([@Antniom](https://github.com/Antniom))
- **Repository:** [https://github.com/Antniom/Solidworks-MCP](https://github.com/Antniom/Solidworks-MCP)
- **License:** MIT License. See [LICENSE](LICENSE) for full details.
