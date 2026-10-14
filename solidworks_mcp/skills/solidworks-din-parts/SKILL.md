---
name: solidworks-din-parts
description: >-
  Parametric generation from scratch of standard DIN/ISO mechanical hardware (hex nuts,
  hex bolts, flat washers, spring washers, pipe flanges) in Dassault Systèmes SolidWorks via the
  MCP server (`solidworks`) without requiring a Toolbox license. Features exact metric engineering
  tables (DIN 934/ISO 4032, DIN 933/ISO 4017, DIN 931/ISO 4014, DIN 125/ISO 7089, DIN 127,
  DIN 2573/EN 1092-1) and automated pure-Python scripts. Triggers on English prompts like
  "din 934 nut", "din 933 bolt", "din 125 washer", "din 2573 flange", "iso 4032", "m16 nut",
  "m12 bolt", "hex fastener", "/solidworks-din-parts" as well as Portuguese (pt-PT) phrases like
  "porca din 934", "parafuso din 933", "anilha din 125", "flange din 2573", "porca m16",
  "parafuso m12", "fixador sextavado", "haste roscada", "anilha de pressão".
---

# DIN/ISO Standard Mechanical Hardware — Parametric Templates for SolidWorks

## When this skill applies
Use this skill whenever a user requests a standard mechanical component by DIN/ISO specification:
- DIN 934 / ISO 4032 Hexagon Nut.
- DIN 933 / ISO 4017 Fully Threaded Hexagon Bolt.
- DIN 931 / ISO 4014 Partially Threaded Hexagon Bolt.
- DIN 125 / ISO 7089 Flat Washer (Form A).
- DIN 127 Helical Spring Lock Washer.
- DIN 2573 / EN 1092-1 Flat Welding Flange (PN6 / PN10 / PN16).

This skill generates native, clean parametric `.sldprt` solid models **from scratch** using SolidWorks MCP and Python COM. It does **not** depend on SolidWorks Toolbox, making it portable across all SolidWorks licenses.

For inserting hardware from the official SolidWorks Toolbox database, see `solidworks-toolbox`.
For general custom parts, see `solidworks-modeling`.
If low-level COM dispatch exceptions occur, consult `legacy-solidworks-mcp`.

> [!IMPORTANT]
> **SELF-EVOLUTION PROTOCOL (ALWAYS EVOLVING)**:
> Whenever you add a new DIN/ISO dimension table, pitch standard, pipe flange rating, or parametric fastener template:
> 1. **Immediately update this `SKILL.md` file** in both:
>    - Global config: `~/.gemini/config/skills/solidworks-din-parts/SKILL.md`
>    - Bundled package skill: `solidworks_mcp/skills/solidworks-din-parts/SKILL.md`
> 2. Add the authoritative table or script to the **Learned Quirks, Workarounds & Recipes** section at the bottom of this file.
> 3. Persist standard fastener engineering tables into the MCP repository via `record_knowledge`.
> 4. If the discovery reveals COM interface changes or pywin32 quirks, also document it in `legacy-solidworks-mcp`.

---

## Tool Selection Hierarchy: Native Tools First

> [!IMPORTANT]
> **STRICT TOOL EXECUTION HIERARCHY**:
> 1. **Priority 1 — Native MCP Tools (Always use first)**:
>    Build DIN/ISO hardware using the dedicated native MCP tools (`create_new_part`, `set_units`, `create_sketch`, `draw_polygon`, `draw_circle`, `extrude_sketch`, `create_sketch_on_face`, `cut_extrude`, `verify_model`, `capture_viewport`).
>    - Native tools provide robust transactional modeling with zero COM boilerplate.
> 2. **Priority 2 — `execute_python` (Security-Gated Fallback ONLY)**:
>    Do **NOT** write raw Python COM scripts when standard MCP sketch/extrude/cut/revolve tools can build the fastener.
>    `execute_python` is locked by default and requires explicit human user consent in chat.
>    Use `execute_python` **ONLY IF**:
>    - High-fidelity physical helical cosmetic threads (via `FeatureManager.InsertHelix` / swept cut) are explicitly requested.
>    - A native tool encounters an unhandled geometry exception.

---

## Core Geometric Equations

For regular 6-sided hex geometry across flats $s$:
- **Circumradius**: $R = \frac{s}{\sqrt{3}} \approx s \times 0.57735$
- **Apothem** (radius to flat midpoint): $a = \frac{s}{2}$
- **30° Revolve Chamfer**:
  - Inner chamfer radius: $r_{\text{in}} = \frac{s}{2} - 0.5\text{ mm}$
  - Outer chamfer radius: $r_{\text{out}} = R + 1.0\text{ mm}$
  - Chamfer axial cut depth: $\Delta z = (r_{\text{out}} - r_{\text{in}}) \times \tan(30^\circ)$

---

## Authoritative Dimension Tables

### 1. DIN 934 (ISO 4032) — Hex Nut
| Thread | Across Flats $s$ (mm) | Height $m$ (mm) | Coarse Pitch $p$ (mm) | Tap Drill $d_{\text{hole}}$ (mm) | Circumradius $R$ (mm) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M5**  | 8.0  | 4.0  | 0.8  | 4.2  | 4.62  |
| **M6**  | 10.0 | 5.0  | 1.0  | 5.0  | 5.77  |
| **M8**  | 13.0 | 6.5  | 1.25 | 6.8  | 7.51  |
| **M10** | 16.0 | 8.0  | 1.5  | 8.5  | 9.24  |
| **M12** | 18.0 | 10.0 | 1.75 | 10.2 | 10.39 |
| **M16** | 24.0 | 13.0 | 2.0  | 14.0 | 13.86 |
| **M20** | 30.0 | 16.0 | 2.5  | 17.5 | 17.32 |
| **M24** | 36.0 | 19.0 | 3.0  | 21.0 | 20.78 |
| **M30** | 46.0 | 24.0 | 3.5  | 26.5 | 26.56 |

### 2. DIN 933 (ISO 4017) — Hex Bolt
| Thread | Across Flats $s$ (mm) | Head Height $k$ (mm) | Standard Lengths $L$ (mm) |
| :--- | :--- | :--- | :--- |
| **M6**  | 10.0 | 4.0  | 12, 16, 20, 25, 30, 40, 50 |
| **M8**  | 13.0 | 5.3  | 16, 20, 25, 30, 40, 50, 60 |
| **M10** | 16.0 | 6.4  | 20, 25, 30, 40, 50, 60, 80 |
| **M12** | 18.0 | 7.5  | 25, 30, 40, 50, 60, 80, 100 |
| **M16** | 24.0 | 10.0 | 30, 40, 50, 60, 80, 100, 120 |
| **M20** | 30.0 | 12.5 | 40, 50, 60, 80, 100, 120, 150 |
| **M24** | 36.0 | 15.0 | 50, 60, 80, 100, 120, 160 |

### 3. DIN 125 (ISO 7089) — Flat Washer
| For Thread | Inner Diameter $d_1$ (mm) | Outer Diameter $d_2$ (mm) | Thickness $t$ (mm) |
| :--- | :--- | :--- | :--- |
| **M5**  | 5.3  | 10.0 | 1.0 |
| **M6**  | 6.4  | 12.0 | 1.6 |
| **M8**  | 8.4  | 16.0 | 1.6 |
| **M10** | 10.5 | 20.0 | 2.0 |
| **M12** | 13.0 | 24.0 | 2.5 |
| **M16** | 17.0 | 30.0 | 3.0 |
| **M20** | 21.0 | 37.0 | 3.0 |
| **M24** | 25.0 | 44.0 | 4.0 |

### 4. DIN 2573 (EN 1092-1 PN6) — Flat Steel Welding Flange
| Nominal Size | Pipe OD (mm) | Flange OD $D$ (mm) | PCD $K$ (mm) | Thickness $b$ (mm) | Bolt Holes ($n \times d_2$) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **DN25**  | 33.7  | 100 | 75  | 12 | $4 \times 11\text{ mm}$ |
| **DN50**  | 60.3  | 140 | 110 | 14 | $4 \times 14\text{ mm}$ |
| **DN80**  | 88.9  | 190 | 150 | 16 | $4 \times 18\text{ mm}$ |
| **DN100** | 114.3 | 210 | 170 | 16 | $4 \times 18\text{ mm}$ |
| **DN150** | 168.3 | 265 | 225 | 18 | $8 \times 18\text{ mm}$ |

---

## Parametric Generation Scripts

### Script 1: DIN 934 Hex Nut via MCP Tools
```json
// Parameters for M16 nut: s = 24 mm, m = 13 mm, R = 13.856 mm, tap drill = 14 mm
connect_solidworks {}
create_new_part {}
set_units {"unit": "mm"}

// 1. Hexagon polygon on Front Plane
create_sketch {"plane": "Front"}
draw_polygon {"cx": 0, "cy": 0, "radius": 13.8564, "sides": 6, "unit": "mm"}
extrude_sketch {"depth": 13.0, "unit": "mm"}

// 2. Center tap bore
create_sketch_on_face {"x": 0, "y": 0, "z": 13.0, "unit": "mm"}
draw_circle {"x": 0, "y": 0, "radius": 7.0, "unit": "mm"} // d = 14mm
cut_extrude {"depth": 13.0, "through_all": true, "unit": "mm"}

// 3. Verify final geometry
verify_model {"expected_dx": 24.0, "expected_dy": 27.7, "expected_dz": 13.0, "tolerance_mm": 0.5}
capture_viewport {"filepath": "screenshots/hex_nut_m16.png", "view_name": "*Isometric"}
```

### Script 2: DIN 933 Hex Bolt via MCP Tools
```json
// Parameters for M12 x 50 bolt: s = 18 mm, k = 7.5 mm, R = 10.392 mm, L = 50 mm
connect_solidworks {}
create_new_part {}
set_units {"unit": "mm"}

// 1. Hexagon head
create_sketch {"plane": "Front"}
draw_polygon {"cx": 0, "cy": 0, "radius": 10.392, "sides": 6, "unit": "mm"}
extrude_sketch {"depth": 7.5, "unit": "mm"}

// 2. Cylindrical shank from underside of head (z = 0)
create_sketch {"plane": "Front"}
draw_circle {"x": 0, "y": 0, "radius": 6.0, "unit": "mm"} // D12
extrude_sketch {"depth": 50.0, "both_directions": true, "unit": "mm"}

verify_model {"expected_dx": 18.0, "expected_dy": 20.8, "expected_dz": 57.5, "tolerance_mm": 0.5}
capture_viewport {"filepath": "screenshots/hex_bolt_m12x50.png", "view_name": "*Isometric"}
```

### Script 3: DIN 125 Flat Washer
```json
// Parameters for M16 washer: d1 = 17 mm, d2 = 30 mm, t = 3 mm
connect_solidworks {}
create_new_part {}
set_units {"unit": "mm"}

create_sketch {"plane": "Front"}
draw_circle {"x": 0, "y": 0, "radius": 15.0, "unit": "mm"} // OD 30mm
extrude_sketch {"depth": 3.0, "unit": "mm"}

create_sketch_on_face {"x": 0, "y": 0, "z": 3.0, "unit": "mm"}
draw_circle {"x": 0, "y": 0, "radius": 8.5, "unit": "mm"} // ID 17mm
cut_extrude {"depth": 3.0, "through_all": true, "unit": "mm"}

verify_model {"expected_dx": 30.0, "expected_dy": 30.0, "expected_dz": 3.0, "tolerance_mm": 0.2}
capture_viewport {"filepath": "screenshots/washer_din125_m16.png", "view_name": "*Isometric"}
```

---

## Escalation & Diagnostics
- For high-precision cosmetic threads or thread spirals via helix features, use `execute_python` with COM scripting.
- If unfamiliar COM dispatch errors arise, consult `legacy-solidworks-mcp`.

---

## Learned Quirks, Workarounds & Recipes (Continuously Evolving)
<!-- Agents: Whenever you add a new hardware template, table, or pitch standard, document it below and sync to both paths -->
1. **Circumradius vs Apothem in `draw_polygon`**: `draw_polygon` expects circumradius $R = s / \sqrt{3}$, NOT half the width across flats $s/2$. Passing $s/2$ produces an undersized hex nut that fails socket fit.
2. **Bolt Head to Shank Centering**: When extruding the shank from the head, sketching on the head's base plane and extruding with `both_directions=True` or placing the sketch on the underside planar face ensures exact axial alignment.

