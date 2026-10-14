---
name: solidworks-modeling
description: >-
  Parametric 3D part modeling in Dassault Systèmes SolidWorks via the MCP server (`solidworks`).
  Design prismatic, turned, shelled, and multi-body parts; create sketches, boss extrusions, cut extrusions,
  revolves, shells, linear/circular patterns, and Hole Wizard features; manage configurations and custom
  properties; select faces/edges declaratively with `select_by_geometry`; manage feature tree state;
  and verify geometry with bounding box assertions, mass properties, and viewport captures.
  Triggers on English prompts like "model part", "create part", "extrude sketch", "cut hole",
  "revolve sketch", "create shell", "create pattern", "hole wizard", "manage feature", "select geometry",
  "fillet edges", "chamfer", "part design", "solid body", "welcome demo", "i restarted", "ready",
  "welcome plaque", "demo model", "/solidworks-modeling" as well as Portuguese (pt-PT)
  phrases like "modelar peça", "criar peça", "extrusão de esboço", "corte extrudido", "revolução",
  "criar casca", "criar padrão", "assistente de furação", "selecionar geometria", "arredondamento", "chanfro".
---

# SolidWorks 3D Part Modeling & Feature Management via MCP

## When this skill applies
Use this skill whenever creating, modifying, inspecting, or verifying individual 3D solid parts (`.sldprt`) in SolidWorks:
- Creating base and secondary solid features: boss/cut extrusions, revolves, shells, linear and circular patterns.
- Creating standard engineering holes using the Hole Wizard (counterbore, countersink, tapped, clearance).
- Applying mechanical blends: fillets and chamfers.
- Precise, deterministic CAD selection using `select_by_geometry` (plane, cylinder, normal vector, area, radius, centroid) without coordinate guessing.
- FeatureManager tree operations via `manage_feature`: suppress, unsuppress, rename, delete (with absorbed sketches), and reorder.
- Part configuration management via `manage_configuration`: list, add, activate, rename, and delete configurations.
- Metadata and custom properties management via `manage_custom_properties`: get, set, delete, and list (including `$PRP:` linked metadata).
- Reading and driving sketch and feature dimensions via `get_dimensions` and `set_dimension`.
- Verifying dimensional correctness via `inspect_model` and `verify_model`.

For assemblies and mates, see `solidworks-assemblies`.
For standard DIN/ISO fasteners, see `solidworks-din-parts` or `solidworks-toolbox`.
For 2D manufacturing drawings and BOMs, see `solidworks-drawings`.
For FEA stress and deformation analysis, see `solidworks-simulation`.
For dialog recovery and rebuild diagnostics, see `solidworks-diagnostics`.

---

## Tool Selection Hierarchy: Native Tools First

> [!IMPORTANT]
> **STRICT TOOL EXECUTION HIERARCHY**:
> 1. **Priority 1 — Native MCP Tools (Always use first)**:
>    Always use dedicated SolidWorks MCP tools. All 66 native tools execute on a dedicated Single-Threaded Apartment (STA) COM dispatcher thread, enforce strict pre- and post-condition checks, and handle automatic unit conversion (`mm`/`degrees` to meters/radians).
> 2. **Priority 2 — `execute_python` (Security-Gated Fallback ONLY)**:
>    Do **NOT** use `execute_python` for operations covered by native tools.
>    `execute_python` is locked by default. If invoked without consent, it returns an explicit directive for the AI agent to ask the human user for affirmative permission in chat, explaining benefits and host-execution security risks.

---

## Complete Modeling Tool Inventory & Parameter Schemas

### 1. Document & Configuration Management
- `create_new_part {}`: Creates a new part from default part template.
- `save_document {"filepath": str}`: Saves active document.
- `manage_configuration`:
  - `"action"`: `"list"`, `"add"`, `"activate"`, `"rename"`, `"delete"`
  - `"name"`: Target configuration name to add, activate, rename, or delete
  - `"new_name"`: (For rename) New configuration name
  - `"description"`, `"comment"`: (For add) Optional metadata
- `manage_custom_properties`:
  - `"action"`: `"list"`, `"get"`, `"set"`, `"delete"`
  - `"name"`: Target property name (e.g., `"Material"`, `"PartNumber"`, `"Description"`, `"Weight"`)
  - `"value"`: (For set) String value or `$PRP:` linked expression (e.g., `"\"SW-Material@@Part1.SLDPRT\""`)
  - `"property_type"`: `"text"`, `"number"`, `"date"`, `"yesno"` (default: `"text"`)
  - `"configuration"`: Empty string `""` for file-level custom properties, or configuration name for config-specific properties

### 2. Sketch Creation & Sketch Entities
- `create_sketch {"plane": "Front" | "Top" | "Right"}`: Creates a 2D sketch on a standard reference plane.
- `create_sketch_on_face {"x": float, "y": float, "z": float, "unit": "mm"}`: Selects planar face and opens a sketch.
- `close_sketch {}`: Explicitly exits sketch mode.
- `get_sketch_status {}`: Returns whether sketch mode is active and entity counts.
- `draw_rectangle {"x1": float, "y1": float, "x2": float, "y2": float, "unit": "mm"}`
- `draw_circle {"x": float, "y": float, "radius": float, "unit": "mm"}`
- `draw_line {"x1": float, "y1": float, "x2": float, "y2": float, "unit": "mm"}`
- `draw_arc {"cx": float, "cy": float, "radius": float, "start_angle": float, "end_angle": float, "unit": "mm"}`
- `draw_polygon {"cx": float, "cy": float, "radius": float, "sides": int, "unit": "mm"}`
- `draw_text {"text": str, "x": float, "y": float, "height": float, "font_name": str, "bold": bool, "unit": "mm"}`

### 3. 3D Solid Features
- `extrude_sketch {"depth": float, "both_directions": bool, "unit": "mm"}`: Extrudes active or selected sketch. Automatically closes sketch and selects profile before extrusion.
- `cut_extrude {"depth": float, "through_all": bool, "both_directions": bool, "unit": "mm"}`: Cuts material using active or selected sketch profile.
- `revolve_sketch {"angle": float, "reverse_dir": bool, "is_thin": bool, "thin_thickness": float, "is_cut": bool, "axis_name": str, "unit": "deg"}`: Revolves sketch around a selected centerline or axis. Angle in degrees (default `360.0`).
- `create_shell {"thickness": float, "outward": bool, "unit": "mm"}`: Creates hollow thin-walled solid body with constant wall thickness.
- `create_pattern`:
  - Linear pattern: `{"pattern_type": "linear", "feature_name": "Cut-Extrude1", "num1": 4, "spacing1": 25.0, "flip_dir1": bool, "unit": "mm"}`
  - Circular pattern: `{"pattern_type": "circular", "feature_name": "Hole1", "num1": 6, "spacing1": 60.0, "angle": 360.0, "equal_spacing": true, "unit": "mm"}`
- `create_hole_wizard`:
  - `{"hole_type": "counterbore" | "countersink" | "hole" | "tap", "standard": "ISO" | "DIN" | "AnsiMetric", "fastener_type": str, "size": "M8", "end_condition": "through_all" | "blind", "depth": float, "diameter": float, "unit": "mm"}`
- `fillet_edges {"radius": float, "unit": "mm"}`: Blends currently selected edges with constant radius fillet.
- `chamfer_edges {"distance": float, "angle": float, "unit": "mm"}`: Breaks currently selected edges with bevel chamfer (default 45 degrees).
- `manage_feature`:
  - `"action"`: `"suppress"`, `"unsuppress"`, `"rename"`, `"delete"`, `"reorder"`
  - `"feature_name"`: Name of target feature in FeatureManager tree
  - `"new_name"`: (For rename) New feature name
  - `"target_feature"`: (For reorder) Target feature name to move relative to
  - `"move_location"`: (For reorder) `"before"`, `"after"`, `"to_top"`, `"to_end"` (default: `"after"`)
  - `"delete_absorbed"`: (For delete) If `true`, deletes absorbed child sketches (default `true`)
  - `"delete_children"`: (For delete) Delete dependent child features (default `false`)
  - `"config_scope"`: Suppression scope: `1` = ThisConfiguration, `2` = AllConfigurations

### 4. Declarative Geometric Selection (`select_by_geometry`)
Never guess blind coordinates or trial-and-error click points. Use `select_by_geometry` to query and select entities programmatically:
```json
{
  "target_type": "face",
  "surface_type": "cylinder",
  "radius": 15.0,
  "radius_tolerance": 0.1,
  "append": false
}
```
Supported schema parameters:
- `target_type`: `"face"`, `"edge"`, `"vertex"` (default: `"face"`)
- `surface_type`: (For faces) `"plane"`, `"cylinder"`, `"cone"`, `"sphere"`, `"torus"`, `"bspline"`
- `curve_type`: (For edges) `"line"`, `"circle"`, `"arc"`, `"bspline"`
- `normal`: `[nx, ny, nz]` target unit vector with `normal_tolerance` (for planar faces)
- `radius`: Cylindrical/spherical face or circular edge radius with `radius_tolerance`
- `length`: Edge length filter with `length_tolerance`
- `point_on_surface`: `[x, y, z]` 3D coordinate lying on target face
- `midpoint`: `[x, y, z]` target edge midpoint
- `point`: `[x, y, z]` target vertex coordinate
- `bbox_min` / `bbox_max`: Bounding box min/max ranges `[x, y, z]`
- `mark`: Integer selection mark (default: `0`)
- `append`: `true` to add to current selection, `false` to clear previous selection first (default: `false`)
- `unit`: `"mm"`, `"inch"`, `"m"`, `"cm"` (default: `"mm"`)

### 5. Inspection & Verification
- `inspect_model {}`: Returns body count, bounding box `[dx, dy, dz]`, volume ($mm^3$), surface area ($mm^2$), center of mass, and rebuild error status.
- `verify_model {"expected_dx": float, "expected_dy": float, "expected_dz": float, "expected_bodies": 1, "tolerance_mm": 0.2}`: Asserts that final geometry matches CAD design specifications.
- `capture_viewport {"filepath": str, "view_name": "*Isometric"}`: Captures crisp rendered viewport PNG.

---

## DFM & Fabrication-First Engineering Rules

1. **Sheet Metal vs. Billet Machining**:
   - Never model sheet metal enclosures by extruding a 100mm solid block and pocketing out 90% of the material.
   - Use constant nominal wall thickness with `create_shell` ($t = 1.5\text{ mm}$ or $2.0\text{ mm}$) or model flanged plate segments with appropriate bend radii ($r \ge t$).
2. **Axisymmetric Turned Parts (Shafts, Bushings)**:
   - Model turned parts using `revolve_sketch` around a centerline, or sequential centered cylindrical extrusions.
   - Always include lead-in chamfers ($1 \times 45^\circ$) for bearing insertion and deburring.
3. **Milled Features**:
   - Internal vertical corners must have fillet radii matching standard rotary endmills ($R \ge 3\text{ mm}$ for $\varnothing 6\text{ mm}$ cutters). Sharp 90° inside corners cannot be milled.
4. **Standard Holes & Fasteners**:
   - Use `create_hole_wizard` for standard clearance and tapped holes rather than plain extruded cuts.
   - Use `create_pattern` (`"linear"` or `"circular"`) for bolt hole patterns rather than drawing repetitive individual circles.

---

## End-to-End Workflow Recipes

### Recipe 0: First-Run 3D Welcome Demonstration Plaque (9-Step Zero-Reasoning Recipe)
Triggered when the user says "I restarted", "ready", "welcome demo", "demo plaque", or right after MCP setup:
```json
// 1. Connect & bring SolidWorks to desktop foreground
connect_solidworks {}

// 2. Initialize fresh part
create_new_part {}

// 3. Base plaque sketch & extrusion
create_sketch {"plane": "Front"}
draw_rectangle {"x1": -130, "y1": -45, "x2": 130, "y2": 45, "unit": "mm"}
extrude_sketch {"depth": 8, "unit": "mm"}

// 4. Inset 3D typography
create_sketch_on_face {"x": 0, "y": 0, "z": 8, "unit": "mm"}
draw_text {"text": "Welcome to\nSolidworks AI", "x": -75, "y": -16, "height": 16, "font_name": "Segoe UI", "bold": true, "unit": "mm"}
extrude_sketch {"depth": 4, "unit": "mm"}

// 5. Render viewport
capture_viewport {"view_name": "*Isometric"}
```

### Recipe 1: Parametric Flanged Bearing Housing with Hole Wizard & Circular Pattern
```json
// 1. Initialize Part & Set Units
connect_solidworks {}
create_new_part {}
set_units {"unit": "mm"}

// 2. Base Mounting Flange
create_sketch {"plane": "Front"}
draw_circle {"x": 0, "y": 0, "radius": 60, "unit": "mm"}
extrude_sketch {"depth": 15, "unit": "mm"}

// 3. Central Bearing Boss
create_sketch_on_face {"x": 0, "y": 0, "z": 15, "unit": "mm"}
draw_circle {"x": 0, "y": 0, "radius": 35, "unit": "mm"}
extrude_sketch {"depth": 40, "unit": "mm"}

// 4. Central Precision Bore
create_sketch_on_face {"x": 0, "y": 0, "z": 55, "unit": "mm"}
draw_circle {"x": 0, "y": 0, "radius": 25, "unit": "mm"}
cut_extrude {"depth": 55, "through_all": true, "unit": "mm"}

// 5. Hole Wizard: Single M6 Counterbore Bolt Hole on PCD 90mm
create_hole_wizard {
  "hole_type": "counterbore",
  "standard": "ISO",
  "fastener_type": "Socket Head Cap Screw",
  "size": "M6",
  "end_condition": "through_all",
  "x": 0,
  "y": 45,
  "unit": "mm"
}

// 6. Circular Pattern: 4x Bolt Holes on Flange
create_pattern {
  "pattern_type": "circular",
  "feature_name": "Hole1",
  "num1": 4,
  "spacing1": 90.0,
  "angle": 360.0,
  "equal_spacing": true
}

// 7. Select Outer Cylindrical Edge and Apply Chamfer
select_by_geometry {
  "target_type": "edge",
  "curve_type": "circle",
  "radius": 35.0,
  "radius_tolerance": 0.5
}
chamfer_edges {"distance": 1.5, "angle": 45.0, "unit": "mm"}

// 8. Custom Properties & Metadata
manage_custom_properties {
  "action": "set",
  "name": "PartNumber",
  "value": "BH-6035-M6",
  "property_type": "text"
}

// 9. Automated Verification & Screenshot
verify_model {"expected_dx": 120, "expected_dy": 120, "expected_dz": 55, "tolerance_mm": 0.5}
capture_viewport {"filepath": "bearing_housing.png", "view_name": "*Isometric"}
```
