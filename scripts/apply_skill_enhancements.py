"""
Script to apply skill enhancements across all repository and plugin locations.
Synchronizes solidworks-modeling, creates solidworks-dfm, enhances solidworks-drawings,
and updates AGENTS.md rules.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

MODELING_ENHANCEMENT = """---

## The Analytic Primitives Rule (Zero Point-by-Point Approximations)

> [!CAUTION]
> **STRICT PROHIBITION: ZERO POINT-BY-POINT CURVE APPROXIMATION**
> Never approximate circular arcs, fillets, slots, cylindrical surfaces, or rounds by emitting discrete piecewise line segments (`draw_line` in a loop) or dense point coordinate tables.

### Why Piecewise Linear Approximations Break CAD Models:
1. **Loss of G1 / G2 Tangent Continuity**: SolidWorks recognizes analytical circles and arcs with true mathematical curvature. Piecewise lines form faceted polygonal chains that cannot maintain tangent continuity.
2. **B-Rep Topology Fragmentation**: Instead of a single cylindrical or toroidal boundary representation (B-Rep) face, the solid body becomes fractured into dozens of micro-facets, severely bloating file size and degrading graphics performance.
3. **Broken Downstream Features**: Geometric filters like `select_by_geometry` (filtering for `surface_type: "cylinder"` or circular edges) will fail completely because the faces are flat polygons. Edge fillets (`fillet_edges`) and Hole Wizard placements will fail or self-intersect.
4. **CAM Toolpath Corruption**: CNC post-processors translate native arcs into circular G-code (`G02`/`G03`), yielding glassy surface finishes. Polygonal faceted approximations force thousands of micro-linear segments (`G01`), causing machine chatter, vibration, and ruined surface tolerances.

### Enforced Geometry Standards:
- **Full Circular Profiles**: Always use `draw_circle {"x": cx, "y": cy, "radius": r, "unit": "mm"}`.
- **Curved Transitions & Slots**: Use `draw_arc {"cx": cx, "cy": cy, "radius": r, "start_angle": theta1, "end_angle": theta2, "unit": "mm"}`.
- **Corner Blends**: Model sharp corners first, then blend them parametrically using `fillet_edges {"radius": r}` on the solid body edges.

---

## Canonical Feature Decomposition Protocol

> [!IMPORTANT]
> **NEVER ATTEMPT A MONOLITHIC SKETCH FOR COMPLEX PARTS**
> Avoid drawing an entire complex component (contour + pockets + bosses + holes + ribs + chamfers) in a single sketch. Monolithic sketches cause self-intersecting loops, ambiguous contours, unsolvable constraint chains, and fragile rebuild failures.

Decompose every complex mechanical component into the canonical 7-tier modeling sequence:

```mermaid
flowchart TD
    Tier1["1. Base Prismatic Body\\nBoss-Extrude (MidPlane or blind)"] --> Tier2["2. Primary Cutouts & Core Cavities\\nCut-Extrude (Through-all or blind)"]
    Tier2 --> Tier3["3. Secondary Bosses, Mounts & Pads\\nFace-anchored Boss-Extrudes"]
    Tier3 --> Tier4["4. Structural Reinforcement Ribs\\nSketch on face + Extrude (merge_result=True)"]
    Tier4 --> Tier5["5. Auxiliary Datum Planes\\nOffset or angled reference planes"]
    Tier5 --> Tier6["6. Angled Flanges & Revolved Bosses\\nFeatures built from auxiliary datums"]
    Tier6 --> Tier7["7. Engineering Finishing Features\\nHole Wizard, Chamfers, and Fillets last"]
```

### The 7 Canonical Feature Tiers:
1. **Base Prismatic Body**:
   - Establish the primary bounding solid using a clean, closed 2D sketch on a standard reference plane (`Front`, `Top`, or `Right`).
   - Use `extrude_sketch` with appropriate symmetry (e.g. `both_directions: true` for parts symmetric across the origin).
2. **Primary Cutouts & Core Cavities**:
   - Cut through-holes, major pockets, and lightening apertures using `cut_extrude`.
   - Maintain clear wall thicknesses between internal pockets and outer boundaries.
3. **Secondary Bosses, Mounts & Pads**:
   - Add localized raised material for bearing housings, bolt standoffs, or alignment tabs.
   - Anchor sketches directly onto existing planar faces.
4. **Structural Reinforcement Ribs**:
   - Add web ribs, gussets, and stiffeners.
   - Sketch directly on the supporting face or central symmetry plane, extrude to meet adjacent walls, ensuring seamless solid body union (`merge_result=True`).
5. **Auxiliary Reference Planes**:
   - When features exist at compound angles or offset elevations, construct dedicated datum reference planes before sketching.
6. **Angled Flanges, Arms & Revolved Bosses**:
   - Build geometric extensions anchored to the auxiliary planes or existing faces.
7. **Engineering Finishing Features (Applied LAST)**:
   - Always apply finishing operations at the very end of the tree:
     - Standard bolt holes via `create_hole_wizard`.
     - Edge deburring with `chamfer_edges`.
     - Stress-relieving transition blends with `fillet_edges`.
   - Applying fillets early fragments edges needed for downstream face anchoring.

---

## Face-Anchored Sketching & Merge Integrity

1. **Face Anchoring**:
   - When building secondary features, do not guess global coordinates or create floating disconnected sketches.
   - Use `create_sketch_on_face {"x": px, "y": py, "z": pz, "unit": "mm"}` by targeting a known surface coordinate from the prior feature, or query the face using `select_by_geometry`.
2. **Single Solid Body Integrity**:
   - SolidWorks features should merge with the primary body by default (`merge_result=True`).
   - Multiple disconnected solid bodies in a part are prone to mating confusion, mass calculation errors, and simulation meshing failures unless multi-body design is explicitly intentional.
   - Verify body count after feature additions using `inspect_model {}` (expected: `body_count: 1`).

"""

DRAWINGS_ENHANCEMENT = """---

## Orthographic Layout Hierarchy & Projection Conventions

Engineering drawings must follow standard multi-view projection hierarchy:
- **Third-Angle Projection** (Default for ASME/US/UK):
  - **Front View**: Placed in the lower-left quadrant ($X \approx 100\\text{ mm}, Y \approx 100\\text{ mm}$ on A3). Represents the primary functional orientation of the part.
  - **Top View**: Vertically aligned directly above the Front view ($X_{\\text{top}} = X_{\\text{front}}, Y_{\\text{top}} > Y_{\\text{front}}$).
  - **Right View**: Horizontally aligned directly to the right of the Front view ($Y_{\\text{right}} = Y_{\\text{front}}, X_{\\text{right}} > X_{\\text{front}}$).
  - **Isometric View**: Placed in the upper-right quadrant ($X \\approx 340\\text{ mm}, Y \\approx 220\\text{ mm}$ on A3), formatted with shaded display mode for instant spatial comprehension.

```mermaid
flowchart TD
    subgraph Sheet["Engineering Drawing Sheet (A3 / A4)"]
        Top["Top View\\n(Aligned vertically above Front)"]
        Front["Front View\\n(Lower-Left Anchor)"]
        Right["Right View\\n(Aligned horizontally to right)"]
        Iso["Isometric View\\n(Upper-Right Shaded)"]
        Title["Title Block & BOM\\n(Lower-Right Corner)"]
    end
```

---

## Model Dimension Import & Annotation Cleanup Protocol

1. **Driving vs. Driven Dimensions**:
   - Always call `import_model_items {"all_views": true}` first. This pulls true parametric driving dimensions directly from 3D sketch and feature constraints into the drawing views.
   - Use `add_drawing_dimension` only for supplementary inspection dimensions or reference dimensions.
2. **Annotation Rules**:
   - Prevent overlapping dimension text and crossing extension lines.
   - Keep dimension callouts outside of the view geometry boundaries whenever possible.
   - Place Section Views (`create_section_view`) along primary planes of symmetry or through internal cavity centers to reveal hidden internal geometry clearly.
   - Assemblies must include a Bill of Materials table (`insert_bom_table`) anchored in the upper-right or lower-right quadrant without obscuring view projections.

"""

DFM_SKILL_CONTENT = """---
name: solidworks-dfm
description: >-
  Design for Manufacturing (DFM) and Design for Additive Manufacturing (DfAM) rules,
  dimensional limits, and fabrication validation heuristics in Dassault Systèmes SolidWorks.
  Validates CNC milling/turning (corner radii, depth-to-width ratios, wall thickness),
  sheet metal bending (bend radii, hole-to-edge distance, relief notches), injection molding
  (uniform wall thickness, draft angles, rib thickness ratios), and 3D printing (45-degree
  overhangs, minimum wall thickness, bridging, horizontal hole compensation).
  Triggers on prompts like "dfm check", "manufacturing check", "machinability", "sheet metal rules",
  "injection molding draft", "3d print overhangs", "cnc radius check", "/solidworks-dfm"
  as well as Portuguese (pt-PT) phrases like "verificação dfm", "regras de fabrico", "maquinabilidade",
  "chapa metálica", "ângulos de saída", "impressão 3d suportes".
---

# Design for Manufacturing (DFM) & Additive (DfAM) in SolidWorks

## When this skill applies
Use this skill whenever designing, modifying, or reviewing SolidWorks CAD parts to ensure they can be economically and reliably manufactured using standard industrial processes:
- **CNC Milling & Turning**: Tool clearances, internal pocket corner radii, aspect ratios, standard thread profiles.
- **Sheet Metal Bending**: Minimum bend radii, hole-to-bend keepout zones, bend relief cutouts.
- **Additive Manufacturing (3D Printing / DfAM)**: Self-supporting overhang limits ($45^\\circ$ rule), minimum wall thicknesses, bridging spans, and horizontal hole diameter compensation.
- **Plastic Injection Molding**: Nominal wall thickness uniformity, draft angles for mold release, core-out strategies, and rib-to-wall thickness ratios to prevent sink marks.

---

## 1. CNC Milling & Turning Rules

### A. Internal Pocket Radii (The Rotary Tool Constraint)
- **Constraint**: Rotary endmills cannot cut sharp $90^\\circ$ inside vertical corners.
- **Rule**: Internal corner radius must be $\\ge R_{\\text{tool}} + 0.5\\text{ mm}$.
  - For standard $\\varnothing 6\\text{ mm}$ endmill: internal fillet $R \\ge 3.5\\text{ mm}$.
  - For standard $\\varnothing 10\\text{ mm}$ endmill: internal fillet $R \\ge 5.5\\text{ mm}$.
  - Adding $+0.5\\text{ mm}$ allows the tool to sweep through the corner without decelerating to zero, reducing chatter and tool breakage.
- **Alternative**: If sharp mating parts must sit in a pocket, model **dogbone** or **T-bone corner reliefs** at inside vertices.

### B. Cavity Depth-to-Width Ratio (Tool Deflection & Chatter)
- **Standard Tooling**: Maximum cavity depth $\\le 4 \\times$ pocket width.
- **Extended Reach Tooling**: Maximum cavity depth $\\le 8 \\times$ pocket width (increases machining cost and surface roughness).
- **Deep Pockets**: If depth $> 4 \\times$ width, incorporate draft ($1^\\circ - 2^\\circ$) or widen the cavity opening.

### C. Minimum Wall Thickness
- **Metals (Aluminum 6061, Steels, Brass)**: Minimum wall thickness $t \\ge 0.8\\text{ mm}$ ($1.5\\text{ mm}$ recommended for tall walls).
- **Plastics (Delrin / POM, ABS, Nylon)**: Minimum wall thickness $t \\ge 1.5\\text{ mm}$ ($2.0\\text{ mm}$ recommended to resist cutting forces).

### D. Turned Parts (Lathe Turning)
- Maintain centered symmetry where possible.
- Provide $1 \\times 45^\\circ$ lead-in chamfers on shaft ends for bearing / bushing insertion.
- Add thread relief undercuts (groove width $\\ge 1.5 \\times$ thread pitch) adjacent to shoulders before external threads.

---

## 2. Sheet Metal & Press Brake Bending Rules

### A. Minimum Bend Radius ($R_{\\text{min}}$)
- Soft Aluminum (5052-H32, 3003-H14) & Low-Carbon Steel: $R \\ge 1.0 \\times T$ (where $T$ is sheet thickness).
- Stainless Steel (304, 316) & Hard Alloys: $R \\ge 2.0 \\times T$.
- Sharp inside bends ($R = 0$) create severe stress concentrations and micro-cracking along the bend line.

### B. Hole-to-Bend Keepout Distance
- Any hole or cutout too close to a bend line will deform into an ellipse during press brake forming.
- **Keepout Rule**: Minimum distance from hole edge to inside bend tangent line:
  $$D_{\\text{hole}} \\ge 2.5 \\times T + R$$

### C. Bend Relief Notches
- Bends that terminate into adjacent solid material tear unless relieved.
- **Relief Rule**: Cut rectangular or teardrop relief notches at both ends of every flange bend:
  - Relief notch width $\\ge T$ (or $\\ge 1.5\\text{ mm}$).
  - Relief notch depth $\\ge R + T$.

### D. Minimum Flange Length
- Short flanges slip off the press brake lower V-die during bending.
- **Minimum Flange Rule**: Total outer flange height $L_{\\text{flange}} \\ge 4.0 \\times T$.

---

## 3. Additive Manufacturing (DfAM - 3D Printing)

### A. The $45^\\circ$ Self-Supporting Rule
- Faces oriented downward at an angle $\\theta > 45^\\circ$ from the build vertical axis require support material, increasing print time, post-processing labor, and surface roughness.
- **Rule**: Self-support chamfers or angles must be $\\le 45^\\circ$ from vertical ($45^\\circ$ from horizontal build plane).

### B. Minimum Wall Thickness by Process
| 3D Printing Process | Minimum Wall ($t_{\\text{min}}$) | Recommended Wall ($t_{\\text{rec}}$) | Minimum Feature Size |
|---|---|---|---|
| **FDM (FFF - PLA, PETG, ABS)** | $1.2\\text{ mm}$ (3 perimeters) | $2.0\\text{ mm}$ | $1.0\\text{ mm}$ |
| **SLS (Nylon PA11 / PA12)** | $0.7\\text{ mm}$ | $1.5\\text{ mm}$ | $0.5\\text{ mm}$ |
| **SLA / DLP (Resin)** | $0.6\\text{ mm}$ | $1.2\\text{ mm}$ | $0.3\\text{ mm}$ |
| **SLM / DMLS (Metal PBF)** | $0.5\\text{ mm}$ | $1.0\\text{ mm}$ | $0.4\\text{ mm}$ |

### C. Horizontal Hole Compensation
- FDM printers consistently shrink horizontal through-holes due to filament polymer tension and slicing polygons.
- **Rule**: Holes with $\\varnothing < 5\\text{ mm}$ should be modeled $+0.2\\text{ mm}$ to $+0.3\\text{ mm}$ oversized, or designed for secondary post-reaming/drilling.
- For unsupported circular holes $\\varnothing > 15\\text{ mm}$ printing horizontally without support, use a **teardrop profile** (top apex angled at $45^\\circ$).

### D. Unsupported Bridging Limits
- Flat horizontal spans (bridges) without supports should not exceed $10\\text{ mm}$ in FDM. Spans $> 10\\text{ mm}$ sag or string.

---

## 4. Plastic Injection Molding Rules

### A. Uniform Nominal Wall Thickness
- Non-uniform walls cool at different rates, producing severe internal stresses, sink marks, and warpage.
- **Rule**: Maintain nominal wall thickness within $\\pm 25\\%$ across the entire part.
- Core out thick sections using `cut_extrude` or `create_shell` rather than modeling solid masses.

### B. Draft Angles for Mold Release
- All vertical surfaces perpendicular to the mold parting plane require draft to allow the part to eject without scuffing or sticking.
- **Standard Walls**: Minimum $1.0^\\circ$ draft ($1.5^\\circ - 2.0^\\circ$ preferred).
- **Deep Cavities (> 50mm)**: $2.0^\\circ - 3.0^\\circ$ draft.
- **Ribs**: $0.5^\\circ - 1.0^\\circ$ draft per side.
- **Textured Surfaces (SPI-C1, Mold-Tech)**: Add $+1.0^\\circ$ draft for every $0.025\\text{ mm}$ of texture depth.

### C. Rib Design & Sink Prevention
- Reinforcement ribs stiffen the part without increasing overall wall thickness.
- **Rib-to-Wall Ratio**: Rib thickness at base must be $50\\%$ to $60\\%$ of nominal wall thickness ($T_{\\text{rib}} \\le 0.6 \\times T_{\\text{wall}}$).
- **Rib Height**: Maximum $3 \\times T_{\\text{wall}}$.
- **Base Radii**: Fillet rib base with $R = 0.25 \\times T_{\\text{wall}}$ to reduce notch stress concentrations while preventing sink.

---

## 5. DFM Audit Checklist & Inspection Recipes

Before finalizing or releasing any SolidWorks model, execute this DFM inspection checklist:

```mermaid
flowchart TD
    Inspect["1. inspect_model\\nVerify volume, bbox, 1 body, zero rebuild errors"] --> CheckGeometry["2. select_by_geometry\\nCheck cylindrical faces, minimum radii, wall thickness"]
    CheckGeometry --> CheckHoles["3. inspect Hole Wizard & Patterns\\nVerify standard drill/tap diameters & pitch clearances"]
    CheckHoles --> CheckDraft["4. Verify Draft / Reliefs\\nEnsure mold draft or sheet metal bend reliefs"]
    CheckDraft --> Finish["5. Final Verification Pass\\nverify_model + capture_viewport"]
```

### DFM Verification Script Example:
```json
// 1. Check Body Count and Bounding Box Integrity
inspect_model {}

// 2. Verify Internal Radii on Vertical Pockets (e.g. radius >= 3.5mm)
select_by_geometry {
  "target_type": "face",
  "surface_type": "cylinder",
  "radius": 3.5,
  "radius_tolerance": 0.5
}

// 3. Confirm Model has Zero Feature Rebuild Errors
inspect_rebuild_errors {}

// 4. Assert Final Model Dimensions within Manufacturing Envelope
verify_model {
  "expected_dx": 120.0,
  "expected_dy": 80.0,
  "expected_dz": 35.0,
  "expected_bodies": 1,
  "tolerance_mm": 0.5
}
```
"""

RULES_AGENTS_CONTENT = """# SolidWorks AI Studio — Agent Rules & Guidelines

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
"""


def update_file_if_changed(path: Path, new_content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == new_content:
            print(f"Unchanged: {path}")
            return
    path.write_text(new_content, encoding="utf-8")
    print(f"Updated: {path}")


def enhance_modeling_skill():
    modeling_paths = [
        REPO_ROOT / "solidworks_mcp" / "skills" / "solidworks-modeling" / "SKILL.md",
        REPO_ROOT / "skills" / "solidworks-modeling" / "SKILL.md",
        REPO_ROOT / "plugins" / "solidworks" / "skills" / "solidworks-modeling" / "SKILL.md",
    ]
    for p in modeling_paths:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8").replace("\r\n", "\n")
        target_marker = "## DFM & Fabrication-First Engineering Rules"
        if "The Analytic Primitives Rule" in text:
            print(f"Already enhanced: {p}")
            continue
        if target_marker in text:
            idx = text.find(target_marker)
            new_text = text[:idx] + MODELING_ENHANCEMENT + text[idx:]
            p.write_text(new_text, encoding="utf-8")
            print(f"Enhanced modeling skill at: {p}")
        else:
            print(f"Warning: target marker not found in {p}")


def enhance_drawings_skill():
    drawings_paths = [
        REPO_ROOT / "solidworks_mcp" / "skills" / "solidworks-drawings" / "SKILL.md",
        REPO_ROOT / "skills" / "solidworks-drawings" / "SKILL.md",
        REPO_ROOT / "plugins" / "solidworks" / "skills" / "solidworks-drawings" / "SKILL.md",
    ]
    for p in drawings_paths:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8").replace("\r\n", "\n")
        target_marker = "## The 5 Rules for SolidWorks Drawings"
        if "Orthographic Layout Hierarchy" in text:
            print(f"Already enhanced: {p}")
            continue
        if target_marker in text:
            idx = text.find(target_marker)
            new_text = text[:idx] + DRAWINGS_ENHANCEMENT + text[idx:]
            p.write_text(new_text, encoding="utf-8")
            print(f"Enhanced drawings skill at: {p}")
        else:
            print(f"Warning: target marker not found in {p}")


def create_dfm_skill():
    dfm_paths = [
        REPO_ROOT / "solidworks_mcp" / "skills" / "solidworks-dfm" / "SKILL.md",
        REPO_ROOT / "skills" / "solidworks-dfm" / "SKILL.md",
        REPO_ROOT / "plugins" / "solidworks" / "skills" / "solidworks-dfm" / "SKILL.md",
    ]
    for p in dfm_paths:
        update_file_if_changed(p, DFM_SKILL_CONTENT)


def update_rules():
    rule_paths = [
        REPO_ROOT / "solidworks_mcp" / "rules" / "AGENTS.md",
        REPO_ROOT / "rules" / "AGENTS.md",
        REPO_ROOT / "plugins" / "solidworks" / "rules" / "AGENTS.md",
    ]
    for p in rule_paths:
        update_file_if_changed(p, RULES_AGENTS_CONTENT)


def main():
    print("Applying skill enhancements...")
    enhance_modeling_skill()
    enhance_drawings_skill()
    create_dfm_skill()
    update_rules()
    print("All skill enhancements applied successfully.")


if __name__ == "__main__":
    main()
