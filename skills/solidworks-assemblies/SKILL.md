---
name: solidworks-assemblies
description: >-
  Assemblies in SolidWorks through the `solidworks` MCP server: inserting components, standard and
  mechanical mates, flexible/rigid subassemblies, driving mates through a motion range, positional
  representations and interference checks. Use for requests like "create assembly", "add mate",
  "insert component", "check interference", "animate the hinge", or "criar montagem", "adicionar relação".
---

# SolidWorks Assembly Design & Constraint Management via MCP

## When this skill applies
Use this skill when building multi-component SolidWorks assemblies (`.sldasm`):
- Inserting parts or subassemblies into an assembly document (`add_component`).
- Constraining components using standard mates: coincident, concentric, parallel, perpendicular, tangent, distance, angle (`add_mate`).
- Selecting mating surfaces deterministically using `select_by_geometry` (filtering cylindrical hole/shaft faces or planar reference surfaces).
- Actuating kinematic strokes parametrically.
- Checking assembly collisions and physical clearances (`check_interference`).
- Managing assembly-level configurations and Bill of Materials metadata (`manage_configuration`, `manage_custom_properties`).
- Verifying overall assembly envelope and component count (`inspect_model`, `verify_model`, `list_components`).

For individual component modeling, see `solidworks-modeling`.
For standard hardware (bolts, nuts, washers), see `solidworks-din-parts` or `solidworks-toolbox`.
For 2D engineering drawings and BOM generation, see `solidworks-drawings`.
For FEA simulation of assemblies, see `solidworks-simulation`.
For dialog and rebuild recovery, see `solidworks-diagnostics`.

---

## Tool Selection Hierarchy: Native Tools First

> [!IMPORTANT]
> **STRICT TOOL EXECUTION HIERARCHY**:
> 1. **Priority 1 — Native MCP Tools (Always use first)**:
>    Always use dedicated SolidWorks MCP tools (`create_new_assembly`, `add_component`, `add_mate`, `check_interference`, `list_components`, `select_by_geometry`, `verify_model`, `capture_viewport`).
>    - Native `add_component` automatically handles document memory pre-loading (`sw.OpenDoc6`) so external parts insert cleanly.
>    - Native `check_interference` automatically handles `InterferenceDetectionManager` COM lifecycle and releases PropertyManager pages via `Done()`.
>    - Native `add_mate` maps standard constraint types without raw VARIANT dispatch errors.
> 2. **Priority 2 — `execute_python` (Security-Gated Fallback ONLY)**:
>    Do **NOT** use `execute_python` for routine assembly insertion or standard mates.
>    `execute_python` is locked by default. If required for unmapped advanced mates (e.g. rack-and-pinion, cam-follower), prompt the user for permission in chat first.

---

## Key Tool Index & Schemas

### 1. Assembly Construction
- `create_new_assembly {}`: Creates an empty assembly from default template.
- `add_component`:
  - `"component_path"`: Full path to `.sldprt` or `.sldasm` on disk.
  - `"x"`, `"y"`, `"z"`: Placement position (defaults to `0.0, 0.0, 0.0`).
  - `"unit"`: `"mm"` (converted automatically to meters for COM).
- `list_components {}`: Lists all components in assembly with their instance names, file paths, suppression state, and fixity (`is_fixed`).

### 2. Mating & Constraints (`add_mate`)
- `mate_type`: `"coincident"`, `"concentric"`, `"perpendicular"`, `"parallel"`, `"tangent"`, `"distance"`, `"angle"`
- `entity1`, `entity2`: Names or references of the two mating entities.
- `comp1`, `comp2`: Component instance names (e.g. `"bracket-1"`, `"shaft-1"`).
- `align`: `"aligned"` (normals in same direction), `"anti_aligned"` (normals opposite), `"closest"`
- `distance`: Float value in `mm` (for `"distance"` mate type).
- `angle`: Float value in degrees (for `"angle"` mate type).
- `flip`: Boolean to reverse alignment orientation.
- `unit`: `"mm"`

### 3. Precision Selection for Mates (`select_by_geometry`)
Never rely on guessed entity coordinates or broken face names. Use `select_by_geometry` to find exact mating features:
- **Cylindrical Shafts & Bores (for Concentric Mates)**:
  ```json
  {
    "target_type": "face",
    "surface_type": "cylinder",
    "radius": 12.0,
    "radius_tolerance": 0.1
  }
  ```
- **Planar Mating Pads (for Coincident Mates)**:
  ```json
  {
    "target_type": "face",
    "surface_type": "plane",
    "normal": [0, 0, 1]
  }
  ```

### 4. Interference & Diagnostics
- `check_interference`:
  - `"treat_coincidence_as_interference"`: `false` (default: only actual body intersections trigger collisions).
  - Returns `has_interference` (bool), `interference_count` (int), and list of interfering components and volumes.
- `inspect_model {}`: Checks overall assembly bounding box, center of mass, and rebuild status.
- `verify_model {"expected_dx": float, "expected_dy": float, "expected_dz": float, "tolerance_mm": 0.5}`

### 5. Kinematics & Mechanism States
- `inspect_assembly_kinematics {}`: Audit of fixed vs floating components, rigid vs flexible subassemblies and mate health. Run it before driving motion.
- `manage_subassembly_kinematics {"action": ..., "occurrence_name": str}`: `"set_grounded"` / `"set_floating"` fix or free a component; `"set_flexible"` / `"set_rigid"` let a subassembly's own mates move (or not) inside the parent; `"enforce_rigidity"` fixes every static component except names matching `"exclude_keywords"`.
- `drive_kinematic_motion {"constraint_name": str, "start_value": float, "end_value": float, "steps": int, "unit": "mm" | "deg", "check_interference": bool}`: Steps a distance or angle mate through a range with a rebuild at each step; with `check_interference` it reports the steps where parts collide. Pass `"unit": "deg"` for angle mates.
- `manage_positional_representations {"action": "list" | "create" | "activate" | "set_override", "name": str, "constraint_name": str, "value": float, "suppress_constraint": bool}`: Named mechanism positions (open/closed, extended/retracted) stored as configurations with mate overrides.
- `delete_assembly_constraint {"constraint_name": str}`: Removes one mate by name (names come from `inspect_assembly_kinematics` or `list_features`).

---

## The 5 Rules for SolidWorks Assemblies

1. **Document Pre-Loading is Mandatory for Components**:
   - In SolidWorks COM, calling `AddComponent4` fails if the referenced part document is not currently loaded into memory.
   - The MCP tool `add_component` automatically handles `sw.OpenDoc6` pre-loading and verified insertion.
2. **Concentric Mates Require Cylindrical Faces, Never Solid Features**:
   - When constraining pins, shafts, or holes, never attempt to select the whole feature (e.g. "Boss-Extrude1").
   - Select the specific cylindrical face using `select_by_geometry {"surface_type": "cylinder", "min_radius": ..., "max_radius": ...}`.
3. **First Component is Fixed by Default**:
   - The first component inserted into a new assembly is automatically fixed at the origin.
   - Secondary components are free (floating) and must be fully constrained with 3 orthogonal mates or mate pairs.
4. **Distance Mates Must Be Strictly Positive**:
   - In SolidWorks COM, passing a negative distance to `AddMate3` / `AddMate5` raises an error.
   - Always pass positive distance values and use `flip: true` to reverse side orientation.
5. **Always Run Interference Checks Before Finalizing**:
   - Run `check_interference {"treat_coincidence_as_interference": false}` before saving. Zero collisions ensures manufactured parts will physically assemble.

---

## Standard Assembly Workflow Recipe

```json
// 1. Initialize Assembly
connect_solidworks {}
create_new_assembly {}
set_units {"unit": "mm"}

// 2. Insert Base Housing Component (Auto-fixed at origin)
add_component {
  "component_path": "C:/Projects/Gearbox_Housing.sldprt",
  "x": 0, "y": 0, "z": 0,
  "unit": "mm"
}

// 3. Insert Rotating Shaft Component (Floating)
add_component {
  "component_path": "C:/Projects/Drive_Shaft.sldprt",
  "x": 0, "y": 50, "z": 100,
  "unit": "mm"
}

// 4. Concentric Mate: Housing Bore to Shaft Journal
add_mate {
  "mate_type": "concentric",
  "comp1": "Gearbox_Housing-1",
  "comp2": "Drive_Shaft-1",
  "entity1": "Cylinder_Bore_Face",
  "entity2": "Shaft_Journal_Face",
  "align": "aligned"
}

// 5. Coincident Mate: Housing Shoulder to Shaft Bearing Retainer Face
add_mate {
  "mate_type": "coincident",
  "comp1": "Gearbox_Housing-1",
  "comp2": "Drive_Shaft-1",
  "entity1": "Shoulder_Flat_Face",
  "entity2": "Shaft_Thrust_Face",
  "align": "anti_aligned"
}

// 6. Collision & Interference Verification
check_interference {"treat_coincidence_as_interference": false}

// 7. Verify Model Bounding Box and Capture Isometric View
verify_model {"expected_dx": 200, "expected_dy": 150, "expected_dz": 300, "tolerance_mm": 1.0}
capture_viewport {"filepath": "assembly_render.png", "view_name": "*Isometric"}
```
