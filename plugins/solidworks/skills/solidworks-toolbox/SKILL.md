---
name: solidworks-toolbox
description: >-
  SolidWorks Toolbox add-in automation, standard library configuration, and hardware insertion
  into assemblies in Dassault Systèmes SolidWorks via the MCP server (`solidworks`). Manages Toolbox
  add-in detection and dynamic loading via `load_addin` (`swBrowser.Browser`), standard hardware selection
  (ISO, DIN, ANSI Metric, ANSI Inch), programmatic insertion into assemblies via `add_component`, configuration
  switching (thread diameter, length, drive type) via `manage_configuration`, and Bill of Materials (BOM)
  part number metadata preservation. Triggers on English prompts like "solidworks toolbox", "insert toolbox hardware",
  "toolbox bolt", "toolbox nut", "bearing library", "toolbox configuration", "/solidworks-toolbox" as well as
  Portuguese (pt-PT) phrases like "biblioteca toolbox", "inserir componente toolbox", "parafuso toolbox",
  "porca toolbox", "rolamento da biblioteca", "configuração de peças standard", "adicionar ferragens toolbox".
---

# SolidWorks Toolbox & Standard Hardware Library via MCP

## When this skill applies
Use this skill when utilizing the official SolidWorks Toolbox standard component database inside assemblies:
- Verifying and dynamically initializing the SolidWorks Toolbox add-in via `load_addin {"name": "toolbox"}`.
- Inserting pre-configured fasteners, washers, nuts, retaining rings, and bearings directly into assemblies (`add_component`).
- Switching Toolbox part configurations using `manage_configuration` (e.g. changing an M12 bolt to M16 or updating length from 40mm to 60mm).
- Managing custom metadata and BOM descriptions using `manage_custom_properties`.
- Constraining Toolbox fasteners to assembly holes and faces using `add_mate` and `select_by_geometry`.

If you do NOT have a SolidWorks Toolbox license or need standalone parametric parts created from scratch, use `solidworks-din-parts`.
For assembly mates and constraints, use `solidworks-assemblies`.
For diagnostics and dialog recovery, use `solidworks-diagnostics`.

---

## Tool Selection Hierarchy: Native Tools First

> [!IMPORTANT]
> **STRICT TOOL EXECUTION HIERARCHY**:
> 1. **Priority 1 — Native MCP Tools (Always use first)**:
>    Always check capabilities and insert components using native MCP tools (`load_addin`, `get_capabilities`, `add_component`, `manage_configuration`, `add_mate`, `check_interference`, `verify_model`, `capture_viewport`).
>    - Native `load_addin {"name": "toolbox"}` dynamically loads `swBrowser.dll` without manual COM hacking.
>    - Native `add_component` ensures the master Toolbox file is properly loaded into memory (`sw.OpenDoc6`) before placement.
>    - Native `manage_configuration` switches configurations safely on the STA thread.
> 2. **Priority 2 — `execute_python` (Security-Gated Fallback ONLY)**:
>    Do **NOT** write raw COM scripts for standard Toolbox operations.
>    `execute_python` is locked by default and requires explicit human user consent in chat.

---

## Standard Toolbox Assembly Workflow

```json
// 1. Ensure Toolbox Add-in is Loaded
connect_solidworks {}
load_addin {"name": "toolbox"}

// 2. Insert Standard Fastener into Assembly
add_component {
  "component_path": "C:/SolidWorks Data/browser/ISO/Bolts and Screws/Hex Head/ISO 4017.sldprt",
  "x": 0, "y": 45, "z": 55,
  "unit": "mm"
}

// 3. Select Target Configuration (M8x30)
manage_configuration {
  "action": "activate",
  "name": "M8x30"
}

// 4. Concentric Mate to Flange Hole via select_by_geometry
select_by_geometry {
  "target_type": "face",
  "surface_type": "cylinder",
  "radius": 4.0,
  "radius_tolerance": 0.1
}
add_mate {
  "mate_type": "concentric",
  "comp1": "Housing-1",
  "comp2": "ISO 4017-1",
  "entity1": "Hole_Cylinder",
  "entity2": "Bolt_Shank",
  "align": "aligned"
}

// 5. Coincident Mate: Bolt Head Underside to Counterbore Seat
add_mate {
  "mate_type": "coincident",
  "comp1": "Housing-1",
  "comp2": "ISO 4017-1",
  "entity1": "Counterbore_Seat",
  "entity2": "Bolt_Washer_Face",
  "align": "anti_aligned"
}

// 6. Verify Clearance & Collisions
check_interference {"treat_coincidence_as_interference": false}
```
