---
name: solidworks-din-parts
description: >-
  Models standard DIN/ISO hardware from scratch as native SolidWorks parts, using exact dimension tables:
  hex nuts (DIN 934 / ISO 4032), hex bolts (DIN 933/931, ISO 4017/4014), washers (DIN 125 / ISO 7089, DIN 127)
  and flat welding flanges (DIN 2573). Use only when the user explicitly wants the standard part itself
  modeled, e.g. "model a DIN 933 M8x40 bolt", "I need the M16 nut as a part file", "we can't download CAD,
  build the DN50 flange", or "modelar parafuso DIN", "desenhar porca sextavada". Otherwise standard parts are
  listed for download (solidworks-purchased-parts).
---

# DIN/ISO hardware built from scratch

> **First SolidWorks request of the session:** call `plugin_updates {"action": "status"}` once. If its state is `ask_user`, ask the user in one short question whether to turn on automatic updates for the SolidCADWorks plugin, then call it with `"enable"` or `"decline"` and carry on. For any other state, say nothing about updates.

**Only when the user asked for the part to be modeled.** By default, standard parts are listed for download
from TraceParts or the maker instead; see `solidworks-purchased-parts`. If the request is ambiguous (e.g.
"add an M16 nut"), follow that skill and mention that you can model the part if they prefer.

These parts are modelled with the ordinary modeling tools, so they work on any SolidWorks licence. To insert
library parts from an installed Toolbox instead, use `solidworks-toolbox`. For general modeling rules, see
`solidworks-modeling`.

Threads are not modelled. Holes use the tap-drill diameter (nuts) or the nominal diameter (bolt shanks).
A real helical thread is only worth it when the user asks for one explicitly; it needs `execute_code`,
which is consent-gated.

## Hex geometry

Use `draw_polygon` with `"flats": true` and `radius = s/2` (s = across flats). The first flat then faces
+X, so the part measures `s` along X and `2s/√3` (across corners) along Y. Without `flats`, `radius` is the
corner radius `s/√3`. Passing `s/2` without `flats` gives a hexagon 13 % too small.

## Dimension tables (mm)

### DIN 934 / ISO 4032 hex nut
| Thread | s | m | Pitch | Tap drill |
| :--- | :--- | :--- | :--- | :--- |
| M5 | 8 | 4 | 0.8 | 4.2 |
| M6 | 10 | 5 | 1.0 | 5.0 |
| M8 | 13 | 6.5 | 1.25 | 6.8 |
| M10 | 16 | 8 | 1.5 | 8.5 |
| M12 | 18 | 10 | 1.75 | 10.2 |
| M16 | 24 | 13 | 2.0 | 14.0 |
| M20 | 30 | 16 | 2.5 | 17.5 |
| M24 | 36 | 19 | 3.0 | 21.0 |
| M30 | 46 | 24 | 3.5 | 26.5 |

### DIN 933 / ISO 4017 hex bolt (fully threaded)
| Thread | s | Head k | Standard lengths L |
| :--- | :--- | :--- | :--- |
| M6 | 10 | 4.0 | 12, 16, 20, 25, 30, 40, 50 |
| M8 | 13 | 5.3 | 16, 20, 25, 30, 40, 50, 60 |
| M10 | 16 | 6.4 | 20, 25, 30, 40, 50, 60, 80 |
| M12 | 18 | 7.5 | 25, 30, 40, 50, 60, 80, 100 |
| M16 | 24 | 10.0 | 30, 40, 50, 60, 80, 100, 120 |
| M20 | 30 | 12.5 | 40, 50, 60, 80, 100, 120, 150 |
| M24 | 36 | 15.0 | 50, 60, 80, 100, 120, 160 |

DIN 931 / ISO 4014 (partly threaded) has the same head; the geometry is identical when threads are not
modelled.

### DIN 125 / ISO 7089 flat washer
| For | d1 | d2 | t |
| :--- | :--- | :--- | :--- |
| M5 | 5.3 | 10 | 1.0 |
| M6 | 6.4 | 12 | 1.6 |
| M8 | 8.4 | 16 | 1.6 |
| M10 | 10.5 | 20 | 2.0 |
| M12 | 13 | 24 | 2.5 |
| M16 | 17 | 30 | 3.0 |
| M20 | 21 | 37 | 3.0 |
| M24 | 25 | 44 | 4.0 |

### DIN 2573 (EN 1092-1, PN6) flat welding flange
| DN | Pipe OD | Flange OD D | PCD K | Thickness b | Holes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| DN25 | 33.7 | 100 | 75 | 12 | 4 × Ø11 |
| DN50 | 60.3 | 140 | 110 | 14 | 4 × Ø14 |
| DN80 | 88.9 | 190 | 150 | 16 | 4 × Ø18 |
| DN100 | 114.3 | 210 | 170 | 16 | 4 × Ø18 |
| DN150 | 168.3 | 265 | 225 | 18 | 8 × Ø18 |

A size or standard not listed here: say so and ask for the values, or cite a source. Don't extrapolate.

## Recipes

Each recipe ends with `verify_model` against the table values. If it fails, fix the part before reporting.

### M16 nut (DIN 934)
```json
create_document {"type": "part"}
set_units {"unit": "mm"}
create_sketch {"plane": "Front"}
draw_polygon {"cx": 0, "cy": 0, "radius": 12.0, "sides": 6, "flats": true, "unit": "mm"}
extrude_sketch {"depth": 13.0, "unit": "mm"}
create_sketch {"face_point": [0, 0, 13.0], "unit": "mm"}
draw_circle {"x": 0, "y": 0, "radius": 7.0, "unit": "mm"}
cut_extrude {"depth": 13.0, "through_all": true, "unit": "mm"}
verify_model {"expected_dx": 24.0, "expected_dy": 27.71, "expected_dz": 13.0, "tolerance_mm": 0.2}
```

### M12 × 50 bolt (DIN 933)
The head sits on z = 0…7.5; the shank goes the other way, z = −50…0.
```json
create_document {"type": "part"}
set_units {"unit": "mm"}
create_sketch {"plane": "Front"}
draw_polygon {"cx": 0, "cy": 0, "radius": 9.0, "sides": 6, "flats": true, "unit": "mm"}
extrude_sketch {"depth": 7.5, "unit": "mm"}
create_sketch {"plane": "Front"}
draw_circle {"x": 0, "y": 0, "radius": 6.0, "unit": "mm"}
extrude_sketch {"depth": 50.0, "reverse_dir": true, "unit": "mm"}
verify_model {"expected_dx": 18.0, "expected_dy": 20.78, "expected_dz": 57.5, "expected_bodies": 1, "tolerance_mm": 0.2}
```

### M16 washer (DIN 125)
```json
create_document {"type": "part"}
set_units {"unit": "mm"}
create_sketch {"plane": "Front"}
draw_circle {"x": 0, "y": 0, "radius": 15.0, "unit": "mm"}
extrude_sketch {"depth": 3.0, "unit": "mm"}
create_sketch {"face_point": [0, 0, 3.0], "unit": "mm"}
draw_circle {"x": 0, "y": 0, "radius": 8.5, "unit": "mm"}
cut_extrude {"depth": 3.0, "through_all": true, "unit": "mm"}
verify_model {"expected_dx": 30.0, "expected_dy": 30.0, "expected_dz": 3.0, "tolerance_mm": 0.2}
```

### Flange (DIN 2573)
1. Extrude a disc of diameter D and thickness b.
2. Cut the bore at the pipe OD.
3. Cut one bolt hole at (0, K/2) on the face.
4. Make a circular pattern of that cut with the hole count.
5. Verify D × D × b and the volume.

## Finishing

Name the file by its designation (e.g. `DIN933_M12x50.SLDPRT`). Set custom properties `Description`
(e.g. "Hex bolt DIN 933 M12x50") and `Material` with `manage_custom_properties`, then `save_document`.

If you work out a new table or a reliable recipe, add it to this file in the repository
(`skills/solidworks-din-parts/SKILL.md`) and save it for later sessions with `record_knowledge`.
