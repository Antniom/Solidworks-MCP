# SolidCADWorks — Agent Rules & Guidelines

## Core Principles

1. **Interactive Design First**:
   - Whenever the user asks to "tweak dimensions", "adjust size", "change parameters", or requests "sliders" / "interface in chat", call `show_interactive_studio`.
   - The studio is an MCP App: clients that support MCP Apps show it inline. Where it does not appear, make the requested changes with `set_dimension` instead.

2. **Analytic Primitives Only (Zero Point-by-Point Approximations)**:
   - Never approximate circular arcs, fillets, cylindrical surfaces, or rounds by emitting discrete piecewise line segments (`draw_line` in a loop) or dense point coordinate tables.
   - Always use native mathematical primitives: `draw_circle`, `draw_arc`, and edge blends (`fillet_edges`, `chamfer_edges`).

3. **Canonical Feature Decomposition**:
   - Never attempt a single monolithic sketch containing all outer contours, holes, ribs, and chamfers.
   - Decompose parts into atomic sequential features: Base Prismatic Solid -> Primary Cutouts -> Secondary Bosses -> Ribs -> Auxiliary Planes -> Finishing Hole Wizard & Fillets.
   - Anchor secondary sketches onto existing planar faces (`create_sketch` with `face_point`) to guarantee automatic solid body merging (`merge_result=True`).

4. **Meaningful Feature & Parameter Naming (Zero Cryptic Features)**:
   - Never leave CAD features with cryptic default names like `Boss-Extrude1`, `Boss-Extrude2`, `Cut-Extrude1` when their engineering purpose is known.
   - Immediately rename features to clear, functional engineering terms (`manage_feature {"action": "rename", "feature_name": "Boss-Extrude1", "new_name": "Hex-Head"}`) so the user understands the FeatureManager tree.
   - When calling `show_interactive_studio`, always pass descriptive `feature_labels` (e.g. `{"Boss-Extrude1": "Hex Bolt Head"}`) and `dimension_labels` (e.g. `{"D1@Boss-Extrude1@Part1.Part": "Head Thickness"}`) so every slider explains what changing it does.

5. **Design for Manufacturing (DFM) Verification**:
   - Maintain DFM/DfAM rules for the target fabrication process (minimum internal corner radii >= tool radius + 0.5mm for CNC milling; sheet metal bend radii >= 1T; 45° self-supporting rule for 3D printing; 1.0°+ draft angles and uniform wall thickness for injection molding).
   - Use `set_dimension` for parametric edits rather than redrawing from scratch.

6. **Ask Before Running Code**:
   - `execute_code` runs arbitrary Python or C# on the user's machine. Use it only when no native tool fits, and only after the user has agreed in chat.

7. **Report Server Problems with `report_issue`**:
   - Whenever a SolidCADWorks tool misbehaves — an error not caused by your arguments, a timeout or hang, a wrong or silently different result, a misleading description or error message — call `report_issue` before you retry or work around it. Do the same with `kind: "missing_tool"` when a tool you needed does not exist.
   - Copy, never paraphrase: the exact tool name, the exact JSON arguments and the exact full output. List every attempt in order, the SolidWorks state (open documents, active feature/sketch, selection, last step), expected vs actual, and your guess at the cause plus a concrete fix, labelled as a guess.
   - Do not report your own mistakes (wrong arguments you then corrected). Reports are sent automatically with paths and the user name redacted; tell the user in one line that you filed one.

8. **Follow-Up Automation**:
   - When the user returns from the interactive studio with adjusted parameters, verify the model with `verify_model`, update associated drawing views with `create_drawing_from_model` or `export_drawing` if requested, and report the new physical properties.
