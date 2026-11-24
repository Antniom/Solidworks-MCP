---
name: solidworks-modeling
description: >-
  Parametric 3D part modeling in SolidWorks through the `solidworks` MCP server: sketches (sketch-local or
  world coordinates), extrudes, cuts, revolves, shells, patterns, mirrors, Hole Wizard, fillets/chamfers, sheet
  metal, swept tubes, multi-body parts, global variables, configurations, custom properties, face/edge selection
  and geometry verification. Use for any request to create, edit or inspect a single part (.sldprt), even when
  the user does not say "SolidWorks", e.g. "model a bracket", "cut a hole", "add a fillet", "sheet metal
  flange", "make this wall 3 mm", or in Portuguese "modelar peça", "extrusão", "corte extrudido", "chapa metálica".
---

# SolidWorks part modeling

> **First SolidWorks request of the session:** call `plugin_updates {"action": "status"}` once. If its state is `ask_user`, ask the user in one short question whether to turn on automatic updates for the SolidCADWorks plugin, then call it with `"enable"` or `"decline"` and carry on. For any other state, say nothing about updates.

The tool schemas already list every argument. This file covers what the schemas cannot: how the tools
behave, in what order to use them, and how to check the result. For a complete worked example, read
[reference/recipes.md](reference/recipes.md).

**Standard and bought parts** (bolts, nuts, washers, bearings, motors…) are not modeled by default. Model
the holes and seats they need, and list the parts for download (`solidworks-purchased-parts`). Model one
only if the user explicitly asks (`solidworks-din-parts`).

Related skills: assemblies → `solidworks-assemblies`; drawings → `solidworks-drawings`; FEA → `solidworks-simulation`; manufacturability
limits → `solidworks-dfm`; dialogs, rebuild errors, a stuck session → `solidworks-diagnostics`.

Use the dedicated tools for everything they cover. `execute_code` is consent-gated (the user must agree in
chat) and is only for operations no tool provides.

## Workflow

1. `get_feature_tree {"detail": "context", "depth": 2}` on an unfamiliar model: open documents, selection
   and feature overview in one call.
2. Build in this order, one feature per sketch:
   1. base body;
   2. main cuts and cavities;
   3. bosses and pads sketched on existing faces;
   4. ribs;
   5. reference planes, then features built on them;
   6. holes, chamfers and fillets last.

   A single sketch for a whole complex part gives self-intersecting loops and rebuilds that break on the
   first edit. Fillets applied early split the faces and edges that later sketches and selections rely on.
3. After every feature, check the result (next section). Fix a wrong feature before building on it.
4. Finish with `inspect_model`, `verify_model` against the intended size, and a `capture_viewport` you
   actually look at.

## Check every feature

Every feature tool appends `| V <volume> (<delta> mm³) | F<faces> E<edges>` to its result. Work out the
expected volume change before the call and compare. A cut that removed nothing, or a boss that went the
wrong way, shows up here first.

- `inspect_model {}`: body count, exact bounding box, volume, area, centre of mass, rebuild errors. Expect
  one body unless multi-body is intended. Stray bodies break mates, mass properties and meshing.
- `verify_model {"expected_dx": 120, "expected_dy": 80, "expected_dz": 35, "expected_bodies": 1, "tolerance_mm": 0.2}`
  checks the final size.
- `list_topology {"kind": "bodies"}` lists each body with its box and volume.

## Sketches

- `create_sketch {"plane": "Front"}` for a standard plane. `create_sketch {"face_point": [0, 0, 15]}`
  sketches on the planar face that contains the point. Anchor secondary features to faces of the existing
  body; don't build floating sketches at guessed coordinates.
- **Sketch-local vs world coordinates.** `x`/`y` arguments are sketch-local, and sketch axes often differ
  from world axes. On the Top plane, sketch y is world −Z; on a face whose normal is −Z, sketch x is world −X.
  When the part lives in a global coordinate system, pass world points instead:
  `corner1`/`corner2` (rectangle), `center` (circle, polygon), `start`/`end` (line),
  `start`/`mid`/`end` (3-point arc), `world_points` (closed profile). A world point must lie on the sketch
  plane within 0.01 mm, otherwise the call fails and names the plane position. Each draw result reports where
  the entity landed in world coordinates.
- `draw_polygon`: `radius` is the circumradius (centre to corner) by default. With `"flats": true` it is the
  inscribed radius, so a hexagon with across-flats `s` is `radius = s/2`.
- `draw_closed_profile {"points": [[x, y], ...]}` handles straight-edged outlines only. Arcs need `draw_arc`.
- Use true circles and arcs, never chains of short lines. Faceted profiles can't be found by
  `surface_type: "cylinder"` selection, break fillets and Hole Wizard placement, and machine badly.

## Features

- `extrude_sketch` / `cut_extrude`: `both_directions: true` makes `depth` the **total** thickness (half
  each side).
- `extrude_sketch` only:
  - `merge: false` keeps the extrusion as a separate body.
  - `draft_angle` tapers the walls.
  - `reverse_dir: true` extrudes to the other side of the sketch plane.
- For a cut sketched inside material, pass `reverse_dir` explicitly and read the reported range.
  Without it, the tool tries both directions and keeps the one that removes material.
- `revolve_sketch` reports the volume change and the X/Y/Z range of the new faces. For a partial revolve,
  check which side it turned to (`reverse_dir`, or `both_directions` for a symmetric one).
- `create_shell {"thickness": 2, "face_indices": [3]}` takes 1-based face indices from
  `list_topology {"kind": "faces"}`. Indices change after every feature, so list them right before.
- `create_pattern`:
  - **Linear:** always pass `direction1` (`"+x"`, `"-z"`, …). Without it the direction is an arbitrary edge.
    The tool flips the direction if the instances went the wrong way.
  - **Circular:** `num1`, `angle`, `equal_spacing`.
  - **Mirror:** with `feature_name` it mirrors those features; without it, all bodies are mirrored and merged
    (`merge: false` keeps the copy separate). Model half of a symmetric part, then mirror.
- **Holes:** for a standard fastener size, use `create_hole_wizard`. It places the hole at the last point
  picked on a planar face, so first call `select {"by": "point", "target_type": "face", "point": [x, y, z]}`.
  The fastener comes from `standard` + `hole_type`; `fastener_type` is ignored. The feature is named
  like "CBORE for M6 Hex Socket Head Cap Screw1": take the name from the result before patterning it.
  A circular pattern uses a pre-selected axis, otherwise the largest cylindrical face.
  `hole_linear` places a plain or tapped hole by distances from two reference faces. Its arguments are
  always millimetres, and `depth: 0` means through all.
- `fillet_edges` / `chamfer_edges` act on the current selection. A `select` that matches nothing clears the
  selection, so the fillet then fails instead of acting on stale edges. Check `total_selection_count` in the
  select result before a multi-edge fillet.
- `sweep_tube {"points": [[0, 0, 0], [0, 0, 100], [80, 0, 100]], "diameter": 8, "bend_radius": 15}` sweeps a
  round solid along a 3D polyline (pipes, wire rods, harnesses). `merge: false` keeps it as a separate body.
- `combine_bodies {"operation": "subtract", "main_body": 1, "tool_bodies": [2, 3]}` (also `add`,
  `intersect`). Body indices come from `list_topology {"kind": "bodies"}`.

## Selection

All selection goes through `select`, with the method chosen by `by`:

| `by` | Use when | Example |
| :--- | :--- | :--- |
| `geometry` | You can describe the entity (type, normal, axis, radius, length). The default. | `select {"by": "geometry", "surface_type": "cylinder", "radius": 5}` |
| `centroid` | You have coordinates from `list_topology` (always mm). `"locate_only": true` returns the index without selecting. | `select {"by": "centroid", "target_type": "edge", "x": 25, "y": 0, "z": 20}` |
| `name` | Named planes, features and bodies. | `select {"by": "name", "target_type": "plane", "name": "Front Plane"}` |
| `point` | The entity under a known 3D point. | `select {"by": "point", "point": [0, 0, 20]}` |
| `path` | You have a topology path from `inspect_topology`. | `select {"by": "path", "path": "Boss-Extrude1/Face[0]/Edge[2]"}` |

- `"append": true` adds to the current selection. `"mark"` sets the selection mark.
- With `by: geometry`:
  - `normal` matches planar faces only.
  - `axis` matches cylinders and cones in either sense. Each matched cylinder reports `radius_mm`,
    `axis_point_mm` (the hole centre) and `span_along_axis_mm`. This is how to read hole patterns off a
    bought STEP part.

## Units

Lengths use the session unit (`mm` unless `set_units` changed it) or a per-call `"unit"`. These are always
millimetres regardless: `hole_linear`, `verify_model`, `select` by centroid, and every argument ending in `_mm`.

## Sheet metal

1. `set_sheet_metal_thickness {"thickness_mm": 1.5, "bend_radius_mm": 1.5, "k_factor": 0.5}`.
2. Sketch the blank, then `sheet_metal_face {}`.
   - A closed profile makes a plate.
   - An open line chain plus `depth` makes a formed section with real bends.
   - Read `sketch_extent` to see which side the thickness went.
3. `list_topology {"kind": "edges"}`, then `flange {"edge_indices": [3], "distance": 25}`.
   - Re-list the edges before every flange: each flange trims the sheet.
   - Where flanges on neighbouring edges meet at a corner, pass `end_offsets: [low, high]` (mm).
   - `plain_wall: true` builds a wall without a bend.
4. Sketch cut-outs on a face, then `sheet_metal_cut {"through_all": true}`.

Not supported: hem, miter flange, form tools. Don't model a sheet-metal enclosure by pocketing a solid
block; use these tools or `create_shell`. Check bend radii and hole-to-bend distances with `solidworks-dfm`.

## Parameters, configurations, properties

- `manage_parameters {"action": "add", "name": "Width", "expression": "100mm"}` adds a global variable that
  several features should follow. Use `set_dimension` for a one-off change. `get_dimensions` lists the
  dimension names.
- `manage_configuration` lists, adds, activates, renames and deletes configurations.
- `manage_custom_properties`: an empty `configuration` means file-level properties.
- `manage_feature`: suppress, rename, delete (`delete_absorbed` also removes the feature's sketches),
  reorder, `set_color`.

## Reading an existing model

- `get_feature_tree {"detail": "parametric"}`: typed feature parameters, equations, sketch loops and
  dimensions.
- `inspect_topology {"path": "Boss-Extrude1/Face[0]", "edges": true}`: edges of a face with coordinates.
- `modify_feature_by_path {"feature_path": "Boss-Extrude1", "depth_mm": 25}`: edit depth, draft or fillet
  radius directly.
- `find_related_files {}`: parent assemblies and drawings.

`detail: context`, `detail: parametric`, `inspect_topology`, `modify_feature_by_path`, `find_related_files`,
`select` by path and `capture_viewport {"mode": "overview"}` run in the bundled .NET runner. If one reports the
runner is unavailable, use the other modes.

## Appearance and images

- `apply_material_appearance` sets a named appearance or a custom `color_rgb`. A material assigned with
  `set_material` brings its own appearance; a part-level custom colour replaces it (the material stays).
- `capture_viewport`:
  - `{"view_name": "*Isometric"}` for one view.
  - `{"mode": "overview"}` for a seven-view sheet, the best final check.
  - `{"mode": "showcase", ...}` for an explicit camera and display style.

## Risky multi-step edits

Wrap them in `transaction {"action": "begin"}` … `{"action": "commit"}`. On failure call
`{"action": "abort"}` and read the result: `success: false` means the model was not fully restored and must
be inspected. See `solidworks-diagnostics`.
