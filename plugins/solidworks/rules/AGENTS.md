# SolidWorks AI Studio — Agent Rules & Guidelines

## Core Principles

1. **Interactive Design First**:
   - Whenever the user asks to "tweak dimensions", "adjust size", "change parameters", or requests "sliders" / "interface in chat", call `show_interactive_studio` immediately.
   - Always embed the resulting `<agent-embed src="file:///..."></agent-embed>` tag in your visible response.

2. **Analytic Primitives Only (Zero Point-by-Point Approximations)**:
   - Never approximate circular arcs, fillets, cylindrical surfaces, or rounds by emitting discrete piecewise line segments (`draw_line` in a loop) or dense point coordinate tables.
   - Always use native mathematical primitives: `draw_circle`, `draw_arc`, and edge blends (`fillet_edges`, `chamfer_edges`).

3. **Canonical Feature Decomposition**:
   - Never attempt a single monolithic sketch containing all outer contours, holes, ribs, and chamfers.
   - Decompose parts into atomic sequential features: Base Prismatic Solid -> Primary Cutouts -> Secondary Bosses -> Ribs -> Auxiliary Planes -> Finishing Hole Wizard & Fillets.
   - Anchor secondary sketches onto existing planar faces (`create_sketch_on_face`) to guarantee automatic solid body merging (`merge_result=True`).

4. **Design for Manufacturing (DFM) Verification**:
   - Maintain DFM/DfAM rules for the target fabrication process (minimum internal corner radii >= tool radius + 0.5mm for CNC milling; sheet metal bend radii >= 1T; 45° self-supporting rule for 3D printing; 1.0°+ draft angles and uniform wall thickness for injection molding).
   - Use `set_dimension` for parametric edits rather than redrawing from scratch.

5. **Follow-Up Automation**:
   - When the user returns from the interactive studio with adjusted parameters, verify the model with `verify_model`, update associated drawing views with `create_drawing_from_model` or `export_drawing` if requested, and report the new physical properties.
