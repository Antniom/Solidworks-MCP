---
name: solidworks-assemblies
description: >-
  Assemblies in SolidWorks through the `solidworks` MCP server: inserting parts, subassemblies and STEP/IGES
  files, positioning and fixing components, standard and mechanical mates, flexible/rigid subassemblies,
  driving mates through a motion range, positional representations and interference checks. Use for any
  multi-component (.sldasm) request, e.g. "create assembly", "add mate", "insert the motor STEP", "check
  interference", "animate the hinge", "does the lid clear when open", "let me move the mechanism", or
  "criar montagem", "adicionar relação".
---

# SolidWorks assemblies

> **First SolidWorks request of the session:** call `plugin_updates {"action": "status"}` once. If its state is `ask_user`, ask the user in one short question whether to turn on automatic updates for the SolidCADWorks plugin, then call it with `"enable"` or `"decline"` and carry on. For any other state, say nothing about updates.

The tool schemas list every argument. This file covers how insertion, mates and checks behave, and how to
verify them.

Fasteners, bearings and other bought parts come from the maker's CAD. List them for download
(`solidworks-purchased-parts`), then insert the STEP files the user downloads. Model them only if the user
asks (`solidworks-din-parts`); use Toolbox only if they ask for it (`solidworks-toolbox`).

Related skills: parts → `solidworks-modeling`;
whole multi-part products → `solidworks-project`; dialogs and stuck sessions → `solidworks-diagnostics`.
`execute_code` is consent-gated. Use it only for mate types no tool provides (rack-and-pinion, cam).

## Inserting components

- `add_component` takes a `.sldprt`, `.sldasm`, STEP, IGES or Parasolid path.
  - A neutral file is imported once, saved as a native copy next to it, and reused on later calls. A large
    STEP can take minutes the first time. A wireframe-only STEP cannot be imported; the error says so.
  - `x`, `y`, `z` place the component's **origin**, not its bounding-box centre.
  - `rotation_deg: [rx, ry, rz]` rotates about the assembly X, then Y, then Z, through the component origin.
  - The result reports the origin, the component axes, the fixed state and the box the component occupies.
    Check that box.
- **Parts modelled in the assembly's own coordinate system** go in at `x = y = z = 0` with `"fixed": true`
  and need no mates. This is the fastest and most reliable layout for designed-in-place products.
- The first component is fixed. Others float until mated or fixed.

## Mates

Faces have no names, so mate faces through the selection:

```json
select {"by": "geometry", "target_type": "face", "surface_type": "cylinder", "radius": 12.0, "component": "Gearbox_Housing-1"}
select {"by": "geometry", "target_type": "face", "surface_type": "cylinder", "radius": 12.0, "component": "Drive_Shaft-1", "append": true}
add_mate {"mate_type": "concentric", "use_selection": true, "align": 0}
```

- In an assembly, `select` searches component bodies and reports centroids and normals in **assembly**
  coordinates. `component` limits the search to one component.
- Pick planar faces with `point_on_surface` (assembly coordinates) or `normal`.
- Named planes can be mated directly:
  `add_mate {"mate_type": "coincident", "entity1": "Top Plane", "comp1": "Housing-1", "entity2": "Top Plane", "comp2": "Shaft-1"}`.
- Concentric mates need the cylindrical **face**, not the feature.
- `align`: `0` aligned, `1` anti-aligned, `2` closest.
- **Distance and angle mates** take positive values only; use `"flip": true` for the other side. They can
  solve to either side. The result lists every component the mate moved, with its box before and after.
  If the box is on the wrong side, `delete_assembly_constraint` and add the mate again with `flip`.
- Fully constrain each floating part (typically three mates), or fix it.

## Checks

- `check_interference {"treat_coincidence_as_interference": false}` before saving. It clears the selection
  first, so the whole assembly is checked. Explain every hit (by design, contact, or a fix needed).
- `inspect_model {}` for the assembly box and rebuild errors. `list_components {}` for instance names,
  paths and fixed state.
- `verify_model` for the overall envelope.

## Motion

1. `inspect_assembly_kinematics {}`: fixed vs floating components, rigid vs flexible subassemblies, and mate
   health. Run it before driving anything.
2. `manage_subassembly_kinematics`:
   - `set_grounded` / `set_floating` fix or free a component.
   - `set_flexible` / `set_rigid` let a subassembly's own mates move in the parent (or not).
   - `enforce_rigidity` fixes everything except names matching `exclude_keywords`.
3. `drive_kinematic_motion {"constraint_name": "Angle1", "start_value": 0, "end_value": 110, "steps": 12, "unit": "deg", "check_interference": true}`
   steps a distance or angle mate through its range and reports the steps that collide. Angle mates need
   `"unit": "deg"`.
4. When the user wants to see or try the motion themselves ("let me move the hinge", "show me where it
   collides"), open the motion scrubber:
   `show_motion_scrubber {"constraint_name": "Angle1", "min_value": 0, "max_value": 110, "unit": "deg"}`.
   In clients with MCP Apps the user drags the mate through the range with a live viewport and a
   collision badge, types exact positions, and sweeps the range for a collision map whose segments jump to
   that position. Take the range from the joint limits in the design, not a guess. Never call
   `motion_action` yourself: it belongs to the view. A message starting "Motion check of" comes from its
   **Send to agent** button: explain each collision range and propose fixes (clearance, stop, limit mate).
5. `manage_positional_representations` stores named states (open/closed) as configurations with mate
   overrides.

Mate names come from `inspect_assembly_kinematics` or `get_feature_tree`.
