---
name: legacy-solidworks-mcp
description: >-
  [LEGACY / FALLBACK ONLY] Low-level operational manual, full COM API tool signatures, and win32com quirks/bugs database.
  DO NOT USE FOR REGULAR CAD TASKS. Consult ONLY if specialized domain skills (solidworks-modeling, solidworks-assemblies,
  solidworks-drawings, solidworks-simulation, solidworks-din-parts, solidworks-toolbox) fail or when debugging low-level COM dispatch exceptions.
---

# SolidWorks MCP Server Skill & Knowledge Base

This skill serves as the comprehensive manual and continuously evolving knowledge base for automating SolidWorks using the `solidworks` MCP server.

> [!IMPORTANT]
> **SELF-EVOLUTION PROTOCOL (ALWAYS EVOLVING)**:
> Whenever you encounter a new behavior, quirk, bug, workaround, API pattern, or workflow while interacting with SolidWorks or the MCP server, you **MUST immediately update this `SKILL.md` file** (`~/.gemini/config\skills\solidworks-mcp\SKILL.md` and `.agents/skills/solidworks-mcp/SKILL.md`) to add what you learned to the **Learned Quirks, Bugs & Workarounds** or **Workflow Recipes** sections.

---

## 1. Quick Reference & Tool Directory

The MCP server is registered under `ServerName: "solidworks"`. Tools are invoked using `call_mcp_tool`.

### Lifecycle & Session
| Tool | Arguments | Purpose / Notes |
| :--- | :--- | :--- |
| `connect_solidworks` | `{}` | Connects to running instance or launches `SLDWORKS.exe`. |
| `get_solidworks_info`| `{}` | Returns version, installation path, and default template locations. |
| `get_capabilities`   | `{}` | Discovers active SolidWorks add-ins (Simulation, PhotoView, Toolbox, Routing, Motion), worker thread health, and active document type. |
| `execute_python`     | `{"code": str, "user_confirmed_risks": bool}` | Runs arbitrary Python script on STA thread. Gated by default behind user consent check; requires user permission in chat or `SW_MCP_ALLOW_EXEC=1`. |

### Document Management
| Tool | Arguments | Purpose / Notes |
| :--- | :--- | :--- |
| `create_new_part`    | `{}` | Creates a new part from default part template. |
| `create_new_assembly`| `{}` | Creates a new assembly document. |
| `create_new_drawing` | `{"paper_size": "A4"}` | Creates a new 2D drawing document (A4, A3, A2, A1, Letter). |
| `open_document`      | `{"filepath": str}` | Opens existing `.sldprt`, `.sldasm`, or `.slddrw`. |
| `save_document`      | `{"filepath": str}` | Saves document via `SaveAs3`. |
| `close_document`     | `{"save": bool}` | Closes active document. Default `save: false`. |
| `get_document_info`  | `{}` | Returns document title, path, type, and units via `com_compat.py`. |
| `list_open_documents`| `{}` | Lists all open documents safely without COM dispatch errors. |
| `list_features`      | `{}` | Traverses feature tree reliably. |
| `manage_configuration`| `{"action": "list"\|"add"\|"activate"\|"rename"\|"delete", "name": str}` | Manages configurations on active document. |
| `manage_custom_properties`| `{"action": "list"\|"get"\|"set"\|"delete", "name": str, "value": any}` | Manages custom and configuration-specific metadata properties. |

### Units & Sketching
| Tool | Arguments | Purpose / Notes |
| :--- | :--- | :--- |
| `set_units`          | `{"unit": "mm"\|"inch"\|"m"\|"cm"}` | Sets default dimension unit. |
| `create_sketch`      | `{"plane": "Front"\|"Top"\|"Right"}` | Creates a sketch on a primary reference plane. |
| `create_sketch_on_face` | `{"x": float, "y": float, "z": float, "unit": "mm"}` | Creates sketch on a planar face passing through point (x, y, z). |
| `draw_rectangle`     | `{"x1": float, "y1": float, "x2": float, "y2": float, "unit": "mm"}` | Corner-to-corner rectangle in active sketch. |
| `draw_line`          | `{"x1": float, "y1": float, "x2": float, "y2": float, "unit": "mm"}` | Single line segment. |
| `draw_circle`        | `{"x": float, "y": float, "radius": float, "unit": "mm"}` | Circle with center and radius. |
| `draw_arc`           | `{"cx": float, "cy": float, "radius": float, "start_angle": deg, "end_angle": deg, "unit": "mm"}` | Arc by center, radius, and angles in degrees. |
| `draw_polygon`       | `{"cx": float, "cy": float, "radius": float, "sides": int, "unit": "mm"}` | Regular inscribed polygon (3 <= sides <= 100). |
| `close_sketch`       | `{}` | Exits active sketch mode. |
| `get_sketch_status`  | `{}` | Diagnostic reporting active sketch, sketch names, and feature count. |

### Features & Operations
| Tool | Arguments | Purpose / Notes |
| :--- | :--- | :--- |
| `extrude_sketch`     | `{"depth": float, "both_directions": bool, "unit": "mm"}` | Boss-Extrude. Automatically closes and selects sketch. |
| `cut_extrude`        | `{"depth": float, "through_all": bool, "both_directions": bool, "unit": "mm"}` | Cut-Extrude removing material. |
| `revolve_sketch`     | `{"angle": float, "reverse_dir": bool, "unit": "deg"}` | Revolves sketch profile around centerline or axis. |
| `create_shell`       | `{"thickness": float, "outward": bool, "unit": "mm"}` | Creates hollow constant-wall thin shell feature. |
| `create_pattern`     | `{"pattern_type": "linear"\|"circular", "feature_name": str, "num1": int, "spacing1": float}` | Linear or circular feature patterning. |
| `create_hole_wizard` | `{"hole_type": str, "standard": str, "size": str, "end_condition": str}` | Industry-standard tapped and clearance holes. |
| `fillet_edges`       | `{"radius": float, "unit": "mm"}` | Fillets currently selected edges. |
| `chamfer_edges`      | `{"distance": float, "angle": float, "unit": "mm"}` | Chamfers currently selected edges. |
| `manage_feature`     | `{"action": "suppress"\|"unsuppress"\|"rename"\|"delete"\|"reorder", "feature_name": str}` | FeatureManager tree operations. |
| `get_dimensions`     | `{"feature_name": str, "dimension_name": str}` | Reads driving and driven dimensions. |
| `set_dimension`      | `{"name": str, "value": float, "units": str}` | Modifies dimensions parametrically. |

### Inspection, Verification & Selection
| Tool | Arguments | Purpose / Notes |
| :--- | :--- | :--- |
| `inspect_model`      | `{}` | Extracts body count, exact bounding box (dx, dy, dz in mm), volume, area, center of mass, and rebuild status. |
| `verify_model`       | `{"expected_dx": float, "expected_dy": float, "expected_dz": float, "expected_bodies": int, "tolerance_mm": 0.2}` | Checks rebuild clean state, body count, and performs rotation-invariant bounding box tolerance matching. |
| `select_entity`      | `{"target_type": "face"\|"plane"\|"feature", "direction": str, "name": str, "mark": int, "append": bool}` | Selection by normal direction, coordinate, or name. |
| `select_by_geometry` | `{"target_type": "face"\|"edge"\|"vertex", "surface_type": str, "radius": float, "normal": list}` | Declarative geometric selection without coordinate guessing. |
| `capture_viewport`   | `{"filepath": str, "view_name": "*Isometric"}` | Exports high-resolution 3D viewport snapshot to PNG/JPG via `SaveAs3`. |

### Assemblies
| Tool | Arguments | Purpose / Notes |
| :--- | :--- | :--- |
| `add_component`      | `{"component_path": str, "x": float, "y": float, "z": float, "unit": "mm"}` | Inserts part/subassembly into active assembly with memory pre-loading. |
| `add_mate`           | `{"mate_type": str, "entity1": str, "entity2": str, "comp1": str, "comp2": str, "align": str, "flip": bool}` | Creates standard assembly mate between component entities. |
| `check_interference` | `{"treat_coincidence_as_interference": bool}` | Detects component collisions, computes interference volumes, and cleanly exits PropertyManager via `Done()`. |
| `list_components`    | `{}` | Lists all components with paths, suppression state, and fixity. |

### Drawings
| Tool | Arguments | Purpose / Notes |
| :--- | :--- | :--- |
| `create_drawing_from_model` | `{"model_path": str, "view_name": "*Isometric", "x": float, "y": float, "paper_size": "A4"}` | Creates drawing from 3D model and places model view. |
| `create_section_view`| `{"x": float, "y": float, "section_label": "A", "parent_view": str}` | Generates section cut view at specified sheet placement. |
| `import_model_items` | `{"all_views": bool}` | Imports driving 3D model dimensions into drawing views. |
| `add_drawing_dimension`| `{"x": float, "y": float, "unit": "mm"}` | Places drawing dimension on sheet. |
| `insert_bom_table`   | `{"x": float, "y": float, "bom_type": "parts_only"\|"top_level"}` | Inserts dynamically resolved BOM table into sheet. |
| `export_drawing`     | `{"filepath": str, "format": "pdf"\|"dxf"\|"dwg"\|"png"\|"jpg"}` | Exports drawing sheet to PDF vector document, CAD vector, or raster image. |

### Simulation
| Tool | Arguments | Purpose / Notes |
| :--- | :--- | :--- |
| `create_simulation_study` | `{"study_name": "StaticStudy", "study_type": "static"}` | Creates new FEA simulation study in active document. |
| `set_study_material`      | `{"material_name": "AISI 1020", "database_path": str}` | Assigns material to CAD model and Simulation solid bodies. |
| `add_study_fixture`       | `{"fixture_type": "fixed", "face_name": str}` | Applies boundary restraint (`fixed`, `immovable`, `roller`, `hinge`, `symmetric`) to face. |
| `add_study_load`          | `{"load_type": "pressure"\|"force", "value": float, "unit": "N", "direction": str}` | Applies pressure or force load to face. |
| `mesh_and_run_simulation` | `{"element_size_mm": 2.5, "tolerance_mm": 0.1}` | Meshes model and executes FEA solver synchronously. |
| `get_simulation_results`  | `{"study_name": str}` | Extracts von Mises stress (MPa), resultant displacement (mm), and engineering sanity checks. |

### Diagnostics, Recovery & Add-ins
| Tool | Arguments | Purpose / Notes |
| :--- | :--- | :--- |
| `inspect_dialogs`        | `{}` | Scans active SolidWorks modal popups and returns buttons. |
| `dismiss_dialog`        | `{"action": "OK"\|"Cancel"\|"Yes"\|"No", "hwnd": int}` | Programmatically clicks resolution button on modal dialog. |
| `inspect_rebuild_errors` | `{}` | Traverses FeatureManager tree and reports error codes. |
| `load_addin`             | `{"name": "simulation"\|"toolbox"}` | Dynamically loads and binds SolidWorks add-in DLLs. |
| `reset_sta_worker`       | `{}` | Gracefully resets hung STA thread without terminating SolidWorks. |
| `create_checkpoint`      | `{"name": str, "description": str}` | Saves named feature tree snapshot before high-risk mutations. |
| `rollback_checkpoint`    | `{"name": str}` | Safely deletes features created after checkpoint in reverse order, restoring state. |
| `search_knowledge`       | `{"query": str}` | Searches local confirmed knowledge base and verified quirks. |
| `record_knowledge`       | `{"id": str, "title": str, "problem": str, "successful_approach": str}` | Persists newly discovered CAD or COM behavior to repository. |

---

## 2. Standard Workflows & Recipes

### Recipe 1: Basic Prismatic Part (e.g. Block, Cube)
```json
1. connect_solidworks {}
2. create_new_part {}
3. set_units {"unit": "mm"}
4. create_sketch {"plane": "Front"}
5. draw_rectangle {"x1": -25, "y1": -25, "x2": 25, "y2": 25, "unit": "mm"}
6. extrude_sketch {"depth": 50, "both_directions": false, "unit": "mm"}
```

### Recipe 2: Sketching on Existing Face & Cut Extrude
To cut a hole or feature into an existing solid face:
1. Identify the 3D coordinate of the face (e.g., center point (0, 0, 50) on a 50 mm block extruded along Z).
2. Call `create_sketch_on_face` with `{"x": 0, "y": 0, "z": 50, "unit": "mm"}`.
3. Draw geometry (e.g., `draw_circle {"x": 0, "y": 0, "radius": 10, "unit": "mm"}`).
4. Call `cut_extrude {"depth": 20, "through_all": false, "unit": "mm"}`.

---

## 3. Learned Quirks, Bugs & Workarounds

1. **`get_document_info` Property vs Method Dispatch**:
   - Historically failed with `'int' object is not callable` in raw COM due to SW 2025 property signatures. In the modern MCP server, `com_compat.py` resolves this automatically. Always call native `get_document_info {}` directly.

2. **`list_open_documents` Document Count Query**:
   - Historically threw when zero documents were open because `GetDocuments()` evaluates to `None`. In the modern MCP server, `com_compat.py` and native `list_open_documents {}` handle empty document lists cleanly without error.

3. **Automatic Sketch Closing in `extrude_sketch` and `cut_extrude`**:
   - MCP tools internally close and select the active sketch.

4. **Units Behavior**:
   - SolidWorks COM API internally works strictly in **meters**, **radians**, and **kilograms**.

5. **Viewport Refresh & Zoom to Fit**:
   - Always run `doc.ViewZoomtofit2()` and `doc.GraphicsRedraw2()` after significant geometry creation.

6. **SW 2025 Python COM Property Access**:
   - Several methods are properties in the SW 2025 COM wrappers:
     - `sw.GetDocumentCount` (integer property, not callable).
     - `doc.GetTitle` (string property, not callable).
     - `face.GetArea`, `face.GetBox`, `sketch.GetSketchSegments` (properties, not callable).
     - `study.RunAnalysis` (runs analysis immediately upon property access).

7. **COM ByRef Parameters and Null Pointers with pywin32**:
   - `SelectByID2` Callout parameter fails with Python `None`. Use `win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)`.
   - Methods with ByRef error/warning codes require `win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)`.

8. **SolidWorks Simulation Automation via COM**:
   - Requires at least one solid body in the part. Mesh & Solve via COSMOSWorks API.

9. **SW 2025 FeatureExtrusion2 (23 Parameters)**:
   - When calling `doc.FeatureManager.FeatureExtrusion2` in SW 2025, exactly 23 arguments are required.

10. **Windows Path Escapes in Python Docstrings**:
    - Use raw string notation `r"..."` or forward slashes `/`.

11. **Assembly Component Insertion Requires Pre-Loading Document (AddComponent4)**:
    - Pre-load documents in memory via `sw.OpenDoc6(path, doc_type, 1, "", err, warn)` before calling `asm.AddComponent4(path, "", x, y, z)`.

12. **Adding Assembly Mates via SolidWorks COM (AddMate3 - 13 Parameters)**:
    - Select features using `feat1.Select2(False, 1)` and `feat2.Select2(True, 1)`.
    - Call `doc.AddMate3(mate_type, align, flip, dist, dist_min, dist_max, angle, angle_min, angle_max, for_positioning_only, lock_rotation, width_mate_option, error_status)`.

13. **Distance Mates Require Positive Values with Flip (AddMate3)**:
    - Passing negative distance fails. Always pass `abs(dist)` and set `flip = True` if offset acts in the opposite direction.

14. **MaterialPropertyValues Requires Explicit VT_ARRAY | VT_R8 VARIANT**:
    - Passing a raw Python list of floats causes 32-bit vs 64-bit stride mismatch in `win32com`. SolidWorks interprets every 8 bytes as a double, resulting in `[0.0, val1, 0.0, val2, ...]` and causing index 7 (transparency) to receive ~0.97, rendering parts 97% transparent/ghost glass.
    - Always wrap the 9-element list with `win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [r, g, b, ambient, diffuse, spec, shininess, trans, emission])`.

15. **Standard Named View IDs (`swStandardViews_e`)**:
    - When calling `doc.ShowNamedView2(Name, ViewId)`:
      - 1 = `swFrontView`, 2 = `swBackView`, 3 = `swLeftView`, 4 = `swRightView`, 5 = `swTopView`, 6 = `swBottomView`, 7 = `swIsometricView`, 8 = `swTrimetricView`, 9 = `swDimetricView`.
    - Passing `1` with `"*Isometric"` actually switches to Front View! Always pass `7` for isometric orientation.

16. **Coordinate Frame & Front Plane Modeling**:
    - To model an appliance standing upright with standard front/top/side views:
      - Sketch on `Front Plane`: Sketch X is horizontal width, Sketch Y is vertical height (Y >= 0).
      - Extrude with Mid Plane (end condition 6) for depth, so Front Plane is the mid-plane of the machine, Right Plane is the center divider, and Top Plane is the ground floor (Y = 0).

18. **Assembly Component Insertion Requires Pre-Loading Document (AddComponent4)**:
    - Pre-load documents in memory via `sw.OpenDoc6(path, doc_type, 1, "", err, warn)` before calling `asm.AddComponent4(path, "", x, y, z)`. Calling `AddComponent4` directly from disk without pre-opening returns `False`.

19. **FeatureExtrusion2 Direction Toggle (Arg 3 Dir vs Arg 2 Flip)**:
    - In `doc.FeatureManager.FeatureExtrusion2(Sd, Flip, Dir, ...)`:
      - Argument 2 (`Flip`) is `FlipSideToCut` (only applies to cuts).
      - Argument 3 (`Dir`) is the Direction Toggle! If `Dir = False`, it extrudes along the sketch normal (+Z / forward). If `Dir = True`, it extrudes in the reverse direction (-Z / backward).
      - Always pass `Dir = True` when extruding into negative depth (e.g. tub backwards from fascia).

20. **Eliminating Sketch Profile Overlays in Shaded Exports**:
    - Construction sketches and profile curves will show up with endpoint circles in viewport exports unless explicitly blanked.
21. **Adding Concentric Mates on Cylindrical Faces (`AddMate3` with `swMateCONCENTRIC`)**:
    - `AddMate3` with `mate_type = 1` (`swMateCONCENTRIC`) requires selecting cylindrical faces, not entire extrude features.
    - In SW 2025 Python COM, `feat.GetFaces` is a tuple property (not a callable method).
    - Iterate over `feat.GetFaces`, query `face.GetSurface.IsCylinder` (property). Once identified on both components, select each with `face.Select2(True, 1)` and call `asm.AddMate3(1, 0, False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, False, False, 0, err)`. Set `lock_rotation = False` to preserve rotational degrees of freedom (e.g. for revolving carousels and hinges).

22. **Enabling Flexible Subassemblies (`swSubAssemblySolveAsFlexible = 1`)**:
    - By default, subassemblies in a top assembly are solved as rigid (`c.Solving == 0`), locking all internal degrees of freedom.
    - Set `c.Solving = 1` on the `Component2` object in the top assembly to solve it as flexible. This allows internal mechanisms (e.g. opening lids, sliding drawers, rotating carousels) to actuate dynamically within the top assembly.

23. **Parametrically Actuating Mates (Angle & Distance Dimensions)**:
    - To actuate a stroke or angle programmatically: find the mate subfeature inside the `MateGroup` feature: `dim = subf.Parameter("D1")`.
    - Update `dim.SystemValue = new_val` (meters for distance, radians for angles) followed by `asm.EditRebuild3`. This enables programmatic opening/closing animations and renders of moving assemblies.

24. **Document Saving via COM (`Save3` vs `Save2`)**:
    - `doc.Save3(options, err, warn)` requires `err` and `warn` to be passed as byref integer variants: `win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)`. Passing raw integers `0, 0` fails with `Type mismatch`.
    - Alternatively, `doc.Save2(True)` can be called directly without byref arguments for standard silent saves.

25. **Advanced Limit Mates (Limit Distance & Limit Angle via `AddMate5`)**:
    - Standard `AddMate3` with min/max = 0 creates static/rigid mates. To allow user-interactive dragging with bounded motion in SolidWorks, use `IAssemblyDoc::AddMate5`:
      - **Limit Distance (`swMateDISTANCE = 5`)**:
        `asm.AddMate5(5, 0, flip, dist_nom, dist_max, dist_min, 0.0, 0.0, 0.0, 0.0, 0.0, False, False, 0, err)`. Creates a `MateLimitDistanceDim` feature with allowable variation `[dist_min, dist_max]`.
      - **Limit Angle (`swMateANGLE = 6`)**:
        `asm.AddMate5(6, 0, flip, 0.0, 0.0, 0.0, 0.0, 0.0, angle_nom, angle_max, angle_min, False, False, 0, err)` (angles in radians). Creates a `MateLimitPlanarAngleDim` feature.
      - **Hinge Axes Creation (`InsertAxis2`)**:
        To establish an exact pivot line for rotating parts (e.g. freezer lids, flap doors), select two perpendicular reference planes (e.g. rear plane and rim plane) and call `doc.InsertAxis2(True)`. Then in the assembly, mate the two `RefAxis` features coincident (`swMateCOINCIDENT = 0`), followed by the `AddMate5` Limit Angle mate.

26. **`CDispatch` Objects Are Always `callable()` in `win32com`**:
    - `win32com.client.CDispatch` implements `__call__` for dynamic default dispatch invocation (`DISPID_VALUE`). Therefore `callable(feat)` always evaluates to `True` for any COM object.
    - Never write fallback code like `feat = feat.GetNextFeature; if callable(feat): feat = feat()`. The call `feat()` attempts default value invocation, raising `(-2147352573, 'Member not found.')` and falsely terminating feature tree loops.
    - In SolidWorks 2025 COM, `feat.GetNextFeature` is a property that directly returns the next `IFeature` object (or `None`). Iterate simply with `feat = feat.GetNextFeature`.

27. **Direct High-Resolution Viewport Capture via `SaveAs3`**:
    - `doc.SaveAs3(filepath, 0, 2)` (where version 0 = `swSaveAsCurrentVersion`, options 2 = `swSaveAsOptions_Silent`) reliably captures the active viewport to `.png` or `.jpg` and returns status code `0` (`swSaveAsStatusOK`).
    - Does not require complex COM variant wrappers or external clipboard capture tools.

28. **Face Surface and Normal Vector Extraction in SW 2025**:
    - `body.GetFaces()` returns a tuple of `Face2` COM objects.
    - `face.GetSurface` is a property returning the `Surface` COM object directly.
    - `surf.IsPlane` is a boolean property (`True`/`False`).
    - `surf.PlaneParams` is a property returning a 6-element tuple `(nx, ny, nz, d, root_x, root_y)` where `(nx, ny, nz)` is the unit normal vector.
    - `face.GetBox` is a property returning `(xmin, ymin, zmin, xmax, ymax, zmax)`.
    - `face.Select2(append, mark)` is a method taking boolean `append` and integer `mark`.
    - Never test `callable()` on `surf` or `face.GetBox` because `CDispatch` objects are always callable and invoking them throws `DISPID_VALUE` Member Not Found (`-2147352573`).

29. **`InterferenceDetectionManager` Requires `Done()`**:
    - Querying `doc.InterferenceDetectionManager` opens the interactive Interference Detection PropertyManager page in SolidWorks.
    - While active, all subsequent component and entity selections (`feat.Select2()`, `SelectByID2()`) fail with `False`.
    - Always call `int_mgr.Done()` after retrieving `interferences = int_mgr.GetInterferences` to close the PropertyManager page and restore normal assembly selection state.

30. **Simulation COM Dispatch (`cosworks.dll` & ProgID)**:
    - Add-in path: `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\Simulation\cosworks.dll`.
    - Type library: `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\Simulation\cosworks.tlb`.
    - Load add-in dynamically: `sw.LoadAddIn(sim_dll)`.
    - Correct ProgID for `GetAddInObject` is `'CosmosWorks.CosmosWorks'` (NOT `'SldWorks.SimulationAddin'`).
    - Access root COSMOSWORKS interface via `addin.COSMOSWORKS` or `addin.CosmosWorks`.
    - Document interface: `cw_doc = cw.ActiveDoc`.
    - Study manager: `sm = cw_doc.StudyManager`.
    - Create study: `sm.CreateNewStudy3(name, study_type, mesh_type, err_var)` with `err_var = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)`.

31. **Simulation Restraints & Loads Argument Conventions**:
    - `lrm.AddRestraint(type, [face], null_dispatch, err_var)`:
      - Param 0: Restraint type (`0 = Fixed`, `1 = Immovable`, `2 = Symmetric`, `3 = Roller`, `4 = Hinge`).
      - Param 1: Array of COM Face objects `[face]`.
      - Param 2: `RefGeom` CANNOT be Python `None`! Must be passed as `win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)`. Passing `None` causes `Type mismatch (parameter 3)`.
      - Param 3: `err_var = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)`.
    - `lrm.AddPressure(0, [face], null_dispatch, err_var)`:
      - `pres.PressureBeginEdit()` is a method.
      - `pres.Unit = 0` (N/m^2), `pres.Value = val`.
      - `pres.PressureEndEdit` is a PROPERTY returning the integer error code (0 = success). Calling it as `pres.PressureEndEdit()` raises `TypeError: 'int' object is not callable`.

32. **Simulation Solver Execution (`RunAnalysis` is a Property in win32com)**:
    - `study.CreateMesh(unit, elem_size, tol)` is a method (`unit = 0` for mm).
    - In win32com, `study.RunAnalysis` is exposed to IDispatch as a PROPERTY getter, NOT a method!
    - Simply evaluating `err = study.RunAnalysis` triggers synchronous FEA solving and returns the error code (0 = success).
    - Calling `study.RunAnalysis()` raises `TypeError: 'int' object is not callable`.

33. **Simulation Results Extraction (`GetMinMaxStress` & `GetMinMaxDisplacement`)**:
    - `results = study.Results`.
    - `GetMinMaxStress(component, elem_num, step, null_dispatch, unit, err_var)`:
      - `component = 9` (`swsStaticNodalStress_VON` von Mises).
      - `unit = 3` (`swsStrengthUnitNewtonPerSquareMillimeter` = MPa).
      - Returns a 4-element tuple: `(min_node, min_stress, max_node, max_stress)`.
    - `GetMinMaxDisplacement(component, step, null_dispatch, unit, err_var)`:
      - `component = 3` (`swsStaticDisplacement_URES` Resultant).
      - `unit = 0` (`swsLinearUnitMillimeters` = mm).
      - Returns a 4-element tuple: `(min_node, min_disp, max_node, max_disp)`.

34. **Feature Rollback Deletion in SW 2025 (`feat.Select2` vs `SelectByID2`)**:
    - Calling `model_doc_ext.SelectByID2(name, "BODYFEATURE", 0, 0, 0, False, 0, None, 0)` fails with `False` when the Callout parameter is Python `None`.
    - Direct COM method `feat.Select2(False, 0)` on the `IFeature` instance reliably selects the feature regardless of active selection state.
    - Subsequent deletion via `doc.EditDelete()` successfully removes the feature.
    - **Crucial Dependency Rule**: Features created after a checkpoint must be rolled back in **strict reverse chronological order** (children/dependent features first, e.g. `Cut-Extrude1` before `Sketch2`), otherwise parent sketches are locked or orphaning errors occur.

35. **Multi-Tier Knowledge System Lifecycle & Persistence**:
    - To prevent automation regressions across SOLIDWORKS releases, operational CAD knowledge follows a strict promotion lifecycle:
      - `discovered`: Candidate quirk observed during execution.
      - `experimentally_verified`: Confirmed via automated standalone test script.
      - `documented`: Recorded in architectural / API documentation.
      - `confirmed`: Validated end-to-end in live CAD session and sealed as immutable regression test.
      - `version_specific`: Behaviors tied to specific service packs (e.g. SW 2025 SP0 vs SP5).
    - All knowledge is serialized to structured JSON under `knowledge/` and indexed in memory for fast retrieval via `search_knowledge`.

36. **`FeatureFillet3` ModelDoc2 vs FeatureManager Interface Signature**:
    - `FeatureFillet3` is on `IModelDoc2` (`doc.FeatureFillet3`), NOT on `IFeatureManager`.
    - Correct signature in SW 2025: `doc.FeatureFillet3(radius_m, True, 0, False, 0, 0, null_obj, False, False)` (9 parameters).
    - Legacy calls on `FeatureManager.FeatureFillet3` with 15 arguments fail with `DISP_E_BADPARAMCOUNT (-2147352562, Invalid number of parameters)`.
    - Passing a null VARIANT (`win32com.client.VARIANT(VT_EMPTY, None)`) for the `Radii` parameter and `True` for tangent propagation creates the fillet cleanly and returns integer `1` on success.

37. **`InsertFeatureChamfer` 8-Parameter Numeric Signature**:
    - Resides on `IFeatureManager`: `doc.FeatureManager.InsertFeatureChamfer(Options, ChamferType, Width, Angle, OtherDist, VertexChamDist1, VertexChamDist2, VertexChamDist3)`.
    - Must be called with 8 numeric parameters: `doc.FeatureManager.InsertFeatureChamfer(0, 1, dist_m, angle_rad, 0.0, 0.0, 0.0, 0.0)`.
    - Legacy signatures passing 7 parameters or boolean flags fail with `DISP_E_BADPARAMCOUNT`.

38. **SolidWorks 2025 Document Inspection Properties in win32com**:
    - `swApp.GetDocuments` is exposed to IDispatch as a PROPERTY returning a tuple of open `IModelDoc2` objects, NOT a callable method. Calling `swApp.GetDocuments()` raises `TypeError: 'tuple' object is not callable`.
    - `swApp.GetFirstDocument` is a PROPERTY that evaluates directly to `None` when document count is 0. Calling `swApp.GetFirstDocument()` raises `TypeError: 'NoneType' object is not callable`.
    - `doc.GetType` is an integer PROPERTY (1=Part, 2=Assembly, 3=Drawing). Calling `doc.GetType()` raises `TypeError: 'int' object is not callable`.

39. **Machine-Readable Stress Testing and False-Success Detection**:
    - Every tool invocation must compare `document_state_before` and `document_state_after` across feature count, body count, rebuild status, and bounding box.
    - An MCP tool reporting `success: True` when feature count did not increase or body count is 0 must be classified as `FALSE_SUCCESS` with severity `CRITICAL`.
    - `FeatureExtrusion2` on Front Plane extrudes in direction +Z. A subsequent cut sketch placed on Front Plane requires `both_directions=True` or `Flip=True` to cut into the solid block rather than empty negative space.

---

## 4. Reusable Script Library (`scripts/sw_helpers.py`)

Local: `.agents/skills/solidworks-mcp/scripts/sw_helpers.py`
Global: `~/.gemini/config/skills/solidworks-mcp/scripts/sw_helpers.py`
