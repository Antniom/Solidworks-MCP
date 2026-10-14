---
name: legacy-solidworks-engineering
description: >-
  [LEGACY / FALLBACK ONLY] Archived high-level CAD verification doctrine and procedural invariants for SolidWorks.
  DO NOT USE FOR REGULAR CAD TASKS. Consult ONLY as a background reference for verification principles if requested.
---

# SolidWorks Engineering Doctrine

This skill guides rigorous, reliable CAD automation and engineering verification in SOLIDWORKS.
Tool schemas and parameter contracts in the `solidworks` MCP server are authoritative.

---

## 1. Core Engineering Principles

1. **Explicit Units Everywhere**:
   - Always specify explicit units (`"mm"`, `"inch"`, `"m"`, `"N/m^2"`, `"MPa"`, `"N"`) on dimensional and force inputs.
   - Never rely on default document unit state.

2. **Declarative Selection Over Brittle Coordinates**:
   - Prefer semantic selection (`direction="top"`, `direction="left"`, or named planes `"Front Plane"`) over hardcoded screen coordinates.
   - Use `select_entity` with declarative filters for robust, topological-invariant selections.

3. **Checkpoints Before High-Risk Mutations**:
   - Before applying complex blends, fillets, thin-wall cuts, or multi-component assemblies, create a named checkpoint (`create_checkpoint {"name": "before_feature"}`).
   - If a feature rebuild fails, immediately call `rollback_checkpoint` to cleanly restore valid model state.

4. **Verify Every Mutation**:
   - Do not assume an extrusion or cut succeeded just because an API returned.
   - Run `verify_model` with expected bounding dimensions (`expected_dx`, `expected_dy`, `expected_dz`), tolerance (`0.2 mm`), and body counts to assert exact geometric correctness.

5. **Simulation Verification Workflow**:
   - Always execute FEA in strict engineering sequence:
     1. Assign verified material (`set_study_material`).
     2. Create study (`create_simulation_study`).
     3. Apply boundary restraints (`add_study_fixture`).
     4. Apply verified loads (`add_study_load`).
     5. Generate mesh and solve (`mesh_and_run_simulation`).
     6. Extract results and assert sanity (`get_simulation_results`).
     7. Capture visual confirmation (`capture_viewport`).

6. **Consult and Contribute to Local Knowledge**:
   - Before attempting unfamiliar COM interfaces or add-in calls, query the local verified knowledge base (`search_knowledge {"query": "..."}`).
   - When uncovering new quirks, record evidence via `record_knowledge` to improve future automation reliability.

---

## 2. Standard Procedural Recipes

### Recipe A: Verified Prismatic Part
```text
1. connect_solidworks {}
2. create_new_part {}
3. create_sketch {"plane": "Front"}
4. draw_rectangle {"x1": 0, "y1": 0, "x2": 100, "y2": 50, "unit": "mm"}
5. extrude_sketch {"depth": 20, "unit": "mm"}
6. verify_model {"expected_dx": 100, "expected_dy": 50, "expected_dz": 20, "tolerance_mm": 0.2}
7. capture_viewport {"filepath": "screenshots/part_verification.png"}
```

### Recipe B: Safe Feature Mutation with Rollback
```text
1. create_checkpoint {"name": "pre_fillet", "description": "Prior to 5mm edge fillet"}
2. fillet_edges {"radius": 5, "unit": "mm"}
3. If fillet fails or rebuild state is dirty:
   -> rollback_checkpoint {"name": "pre_fillet"}
```

### Recipe C: Structural FEA Simulation & Sanity Check
```text
1. set_study_material {"material_name": "AISI 1020"}
2. create_simulation_study {"study_name": "Cantilever_Static", "study_type": "static"}
3. add_study_fixture {"fixture_type": "fixed", "direction": "left"}
4. add_study_load {"load_type": "pressure", "value": 100000.0, "unit": "N/m^2", "direction": "top"}
5. mesh_and_run_simulation {"element_size_mm": 2.5, "tolerance_mm": 0.1}
6. get_simulation_results {}
7. capture_viewport {"filepath": "screenshots/stress_plot.png"}
```
