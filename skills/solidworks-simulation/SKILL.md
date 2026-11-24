---
name: solidworks-simulation
description: >-
  FEA in SolidWorks Simulation through the `solidworks` MCP server: static, frequency, buckling and thermal
  studies, study materials, fixtures, force and pressure loads, meshing, solving, and reading von Mises stress,
  displacement and factor of safety. Use for any request to check strength or stiffness of a SolidWorks part,
  even without the word FEA, e.g. "stress analysis", "will this bracket hold 500 N", "factor of safety",
  "how much does it bend", or "análise de tensões", "simulação estática".
---

# SolidWorks Simulation (FEA)

> **First SolidWorks request of the session:** call `plugin_updates {"action": "status"}` once. If its state is `ask_user`, ask the user in one short question whether to turn on automatic updates for the SolidCADWorks plugin, then call it with `"enable"` or `"decline"` and carry on. For any other state, say nothing about updates.

The tool schemas list every argument. This file covers how the tools pick faces, which units they use, and
how to judge the result.

Related skills: modeling → `solidworks-modeling`; dialogs, add-in loading, a stuck solve →
`solidworks-diagnostics`. `execute_code` is consent-gated. Use it only for study types or loads no tool
provides.

## How fixtures and loads find their face

`add_study_fixture` and `add_study_load` resolve the face themselves. **They ignore anything selected
beforehand with `select`.** Name the face in the call with one of these:

- `face_point: [x, y, z]`: a point on the face, in the call's `unit` (default mm). This is the most reliable.
  Read the coordinates from `inspect_model` (bounding box) or `list_topology {"kind": "faces"}`.
- `direction`: the outermost face whose normal points that way (`"left"` = −X, `"right"` = +X,
  `"bottom"` = −Y, `"top"` = +Y, `"back"` = −Z, `"front"` = +Z).
- `face_name`: a named face or entity.

For a **force** load, `direction` is also the force direction ("bottom" pushes towards −Y). So give the
face with `face_point` and the direction separately. With `direction` alone, the same word picks the face
and the force direction, which is rarely what you want. If no directional reference can be found, the tool
falls back to a force normal to the face. Pressure always acts normal to the face.

Fixture types: `fixed` (all DOF), `immovable` (translations only), `roller`, `hinge`, `symmetric`.

## Units and defaults

- `add_study_load` defaults to `load_type: "pressure"` in `N/m^2`. For a force, pass `"load_type": "force"`
  and `"unit": "N"` explicitly.
- Pressure units: `N/m^2`, `Pa`, `psi`, `MPa`. Force units: `N`, `lbf`.
- Results come back in MPa (stress) and mm (displacement).

## Workflow

1. The part must have a material. Use `set_study_material` (e.g. `"AISI 1020"`, `"6061-T6"`); the factor
   of safety depends on it.
2. `create_simulation_study`, then fixtures, then loads.
3. `mesh_and_run_simulation`. It uses parabolic (10-node) tetrahedra. Set `element_size_mm` to about a third
   of the thinnest wall, or leave it to the automatic size.
4. `get_simulation_results`: `max_von_mises_mpa`, `max_displacement_mm`, `min_factor_of_safety`.
5. Sanity-check before reporting:
   - Compare the displacement with a hand estimate (cantilever: δ = F·L³ / (3·E·I)). If it is off by 10×,
     a fixture or load landed on the wrong face; re-read the `target` each call reported.
   - Stress peaks at sharp re-entrant corners are mesh singularities. Report them as such rather than as
     the part's real stress.
   - For ductile metals, the part passes when σ_max ≤ σ_yield / FOS_target. FOS ≥ 1.5 is a common static
     target; ask the user if they have their own.

## Example: 200 × 20 × 20 mm steel cantilever, root at x = 0, tip at x = 200, 500 N downward at the tip

```json
create_simulation_study {"study_name": "Cantilever", "study_type": "static"}
set_study_material {"material_name": "AISI 1020"}
add_study_fixture {"fixture_type": "fixed", "face_point": [0, 10, 10]}
add_study_load {"load_type": "force", "value": 500.0, "unit": "N", "face_point": [200, 10, 10], "direction": "bottom"}
mesh_and_run_simulation {"element_size_mm": 4.0, "tolerance_mm": 0.2}
get_simulation_results {}
capture_viewport {"filepath": "cantilever_stress.png", "view_name": "*Isometric"}
```

Hand check for this beam: I = 20⁴/12 ≈ 13 333 mm⁴, E ≈ 200 000 MPa, so δ ≈ 500·200³ / (3·200 000·13 333)
≈ 0.5 mm, and σ = M·c / I ≈ 500·200·10 / 13 333 ≈ 75 MPa.
