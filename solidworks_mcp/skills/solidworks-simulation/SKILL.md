---
name: solidworks-simulation
description: >-
  Finite Element Analysis (FEA) structural, frequency, and thermal simulation in Dassault Systèmes
  SolidWorks via the MCP server (`solidworks`). Manages COSMOSWorks add-in initialization,
  static studies, engineering material assignment, boundary restraints (fixed, roller, hinge),
  structural loads (pressure, force, directional loads), meshing parameters, and solver results extraction
  (von Mises stress, resultant displacement, factor of safety). Triggers on English prompts
  like "fea simulation", "stress analysis", "von mises", "finite element", "mesh study",
  "run simulation", "displacement", "structural load", "/solidworks-simulation" as well as
  Portuguese (pt-PT) phrases like "simulação fea", "análise de tensões", "tensão de von mises",
  "elementos finitos", "gerar malha", "executar simulação", "deslocamento", "carga estrutural",
  "coeficiente de segurança".
---

# SolidWorks FEA Simulation & Stress Analysis via MCP

## When this skill applies
Use this skill for conducting engineering FEA simulation studies inside SolidWorks:
- Creating Static, Frequency, Buckling, or Thermal simulation studies (`create_simulation_study`).
- Assigning physical engineering materials from standard or custom databases (`set_study_material`).
- Applying boundary restraints and fixtures: fixed faces, roller/slider, hinge, symmetry (`add_study_fixture`).
- Applying mechanical loads: uniform pressure, concentrated normal forces, directional transverse loads (`add_study_load`).
- Declarative selection of load and restraint faces using `select_by_geometry` (e.g. `normal: [-1, 0, 0]` for cantilever fixed end).
- Generating solid parabolic tetrahedral meshes with custom element sizes (`mesh_and_run_simulation`).
- Solving the FEA system and extracting maximum von Mises stress (MPa), resultant displacement (mm), and Factor of Safety (`get_simulation_results`).

For CAD part modeling, see `solidworks-modeling`.
For assembly structure and mates, see `solidworks-assemblies`.
For 2D manufacturing drawings, see `solidworks-drawings`.
For dialog and rebuild recovery, see `solidworks-diagnostics`.

---

## Tool Selection Hierarchy: Native Tools First

> [!IMPORTANT]
> **STRICT TOOL EXECUTION HIERARCHY**:
> 1. **Priority 1 — Native MCP Tools (Always use first)**:
>    Always use dedicated SolidWorks MCP tools (`set_study_material`, `create_simulation_study`, `add_study_fixture`, `add_study_load`, `mesh_and_run_simulation`, `get_simulation_results`, `select_by_geometry`, `capture_viewport`).
>    - Native tools automatically initialize and dynamically load `cosworks.dll`.
>    - They execute on the dedicated STA COM thread, preventing threading apartments collisions.
>    - They handle complex COSMOSWorks VARIANT wrapping (e.g. null dispatch for `RefGeom`) automatically.
>    - They handle unit parsing ($N/m^2$ to Pa, mm to m) safely.
> 2. **Priority 2 — `execute_python` (Security-Gated Fallback ONLY)**:
>    Do **NOT** write raw COSMOSWorks Python scripts for standard static studies, loads, fixtures, or results.
>    `execute_python` is locked by default and requires explicit human user consent in chat.

---

## Complete Simulation Tool Inventory & Schemas

### 1. Study Setup & Materials
- `create_simulation_study`:
  - `"study_name"`: String name (e.g. `"Static-1"`).
  - `"study_type"`: `"static"` (0), `"frequency"` (1), `"buckling"` (2), `"thermal"` (3).
- `set_study_material`:
  - `"material_name"`: Material name matching database (e.g. `"AISI 1020"`, `"6061-T6"`, `"Plain Carbon Steel"`, `"Stainless Steel 304"`).
  - `"database_path"`: Optional path to `.sldmat` file (auto-detected dynamically from SolidWorks install if omitted).
  - `"config_name"`: Target configuration (empty string for active).

### 2. Boundary Restraints (`add_study_fixture`)
- `"fixture_type"`:
  - `"fixed"`: Fixes all translational and rotational degrees of freedom (6 DOF).
  - `"immovable"`: Zero translation, rotation free (3 DOF).
  - `"roller"`: Translates freely along surface plane; constrained normal to face.
  - `"hinge"`: Constrains radial and axial displacement; permits rotation around cylindrical axis.
  - `"symmetry"`: Normal displacement and in-plane rotations zero.
- `"face_name"`: Optional face name or entity identifier. Prefer selecting faces via `select_by_geometry` before calling.
- `"face_point"`: Optional 3D coordinate on target face `[x, y, z]`.
- `"study_name"`: Target study name (empty for active study).

### 3. Structural Loads (`add_study_load`)
- `"load_type"`: `"force"` or `"pressure"` (default: `"force"`).
- `"value"`: **Required** magnitude of load.
- `"unit"`:
  - For pressure: `"N/m^2"` (or `"Pa"`), `"psi"`, `"MPa"`
  - For force: `"N"`, `"lbf"`
- `"direction"`: Load direction or orientation.
- `"face_name"`: Optional face name. Prefer selecting faces via `select_by_geometry` before calling.
- `"face_point"`: Optional 3D coordinate on target face `[x, y, z]`.
- `"study_name"`: Target study name (empty for active study).

### 4. Meshing, Solving & Results
- `mesh_and_run_simulation`:
  - Automatically meshes the solid body with parabolic 10-node tetrahedral elements and executes the COSMOSWorks FEA solver:
  - `"element_size_mm"`: Nominal element edge length in `mm` (defaults to auto-calculated curvature size).
  - `"tolerance_mm"`: Mesh tolerance in `mm`.
  - `"study_name"`: Target study name.
- `get_simulation_results`:
  - Solves or queries the active study and extracts engineering metrics:
  - `"study_name"`: Target study name (empty for active study).
  - Returns `max_von_mises_mpa`, `max_displacement_mm`, `min_factor_of_safety`, and node/element counts.

---

## The 5 Rules for SolidWorks FEA

1. **Always Use Parabolic Tetrahedral Elements**:
   - First-order linear tetrahedra exhibit severe shear locking in bending. The MCP mesher automatically requests high-quality parabolic (10-node) solid elements.
2. **Deterministic Restraint Selection**:
   - Never guess face names for boundary conditions. Use `select_by_geometry` with normal vectors (e.g. `normal: [-1, 0, 0]` for the cantilever fixed root face).
3. **Reference Geometry Null Dispatch Requirement**:
   - In raw COM, `ICWLoadsAndRestraintsManager.AddRestraint` fails if `RefGeom` is passed as empty integer `0` instead of `None` / `pythoncom.Empty`. The native MCP tool encapsulates this bug fix permanently.
4. **Stress Output Units**:
   - COSMOSWorks raw COM returns stress in $N/m^2$ (Pascals). The MCP tool automatically converts to **MPa** ($N/mm^2$) for standard mechanical engineering interpretation.
5. **Yield Strength & Factor of Safety (FOS)**:
   - For ductile metals (steels, aluminum alloys), ensure maximum von Mises stress $\sigma_{\text{max}} \le \sigma_{\text{yield}} / \text{FOS}_{\text{target}}$ (typically $\text{FOS} \ge 1.5$ for static structures).

---

## Standard Static FEA Workflow Recipe

```json
// 1. Initialize SolidWorks and Part Model
connect_solidworks {}

// 2. Initialize Static Simulation Study
create_simulation_study {
  "study_name": "Cantilever_Bending_Analysis",
  "study_type": "static"
}

// 3. Assign Structural Steel Material
set_study_material {
  "material_name": "AISI 1020"
}

// 4. Select Cantilever Root Face and Apply Fixed Support Fixture
select_by_geometry {
  "target_type": "face",
  "surface_type": "plane",
  "normal": [-1, 0, 0]
}
add_study_fixture {
  "fixture_type": "fixed"
}

// 5. Select Tip Face and Apply Downward Transverse Load (5000 N)
select_by_geometry {
  "target_type": "face",
  "surface_type": "plane",
  "normal": [0, 1, 0]
}
add_study_load {
  "load_type": "force",
  "value": 5000.0,
  "unit": "N",
  "direction": "down"
}

// 6. Mesh Solid and Run Analysis
mesh_and_run_simulation {
  "element_size_mm": 5.0,
  "tolerance_mm": 0.25
}

// 7. Extract von Mises Stress and Resultant Deflection
get_simulation_results {}

// 8. Capture Rendered Stress Contour Viewport
capture_viewport {
  "filepath": "stress_contour_plot.png",
  "view_name": "*Isometric"
}
```
