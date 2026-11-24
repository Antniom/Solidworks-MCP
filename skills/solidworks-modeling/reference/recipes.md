# Worked example: flanged bearing housing

This example builds a flange, a boss, a bore, a Hole Wizard hole with a circular pattern, a chamfer,
custom properties, and the final checks. Comments give the expected volume changes, so each step can be
checked against the `| V ... (delta)` the tool reports.

```json
// 1. New part, millimetres
connect_solidworks {}
create_document {"type": "part"}
set_units {"unit": "mm"}

// 2. Flange: Ø120 x 15 (V ≈ +169 646 mm³)
create_sketch {"plane": "Front"}
draw_circle {"x": 0, "y": 0, "radius": 60, "unit": "mm"}
extrude_sketch {"depth": 15, "unit": "mm"}

// 3. Boss on the flange face: Ø70 x 40 (V ≈ +153 938 mm³)
create_sketch {"face_point": [0, 0, 15], "unit": "mm"}
draw_circle {"x": 0, "y": 0, "radius": 35, "unit": "mm"}
extrude_sketch {"depth": 40, "unit": "mm"}

// 4. Bore Ø50 through all (V ≈ -107 992 mm³)
create_sketch {"face_point": [0, 0, 55], "unit": "mm"}
draw_circle {"x": 0, "y": 0, "radius": 25, "unit": "mm"}
cut_extrude {"depth": 55, "through_all": true, "unit": "mm"}

// 5. One M6 counterbore on the flange face at PCD 90: pick the point first, then the Hole Wizard
select {"by": "point", "target_type": "face", "point": [0, 45, 15]}
create_hole_wizard {"hole_type": "counterbore", "standard": "ISO", "size": "M6", "end_condition": "through_all"}

// 6. Four holes in total around the bore axis (V ≈ -3 x 902 mm³). Use the feature_name the Hole Wizard
//    result returned; it is like "CBORE for M6 Hex Socket Head Cap Screw1", not "Hole1". With no axis
//    pre-selected, the largest cylindrical face (here the boss, coaxial with the bore) is the axis.
create_pattern {"pattern_type": "circular", "feature_name": "CBORE for M6 Hex Socket Head Cap Screw1", "num1": 4, "spacing1": 90.0, "angle": 360.0, "equal_spacing": true}

// 7. 1.5 mm chamfer on the boss's outer top edge (check total_selection_count = 1 first)
select {"by": "geometry", "target_type": "edge", "curve_type": "circle", "radius": 35.0, "radius_tolerance": 0.5, "bbox_min": [-36, -36, 54], "bbox_max": [36, 36, 56]}
chamfer_edges {"distance": 1.5, "angle": 45.0, "unit": "mm"}

// 8. Metadata
manage_custom_properties {"action": "set", "name": "PartNumber", "value": "BH-6035-M6", "property_type": "text"}

// 9. Final checks, then look at the image
verify_model {"expected_dx": 120, "expected_dy": 120, "expected_dz": 55, "expected_bodies": 1, "tolerance_mm": 0.5}
capture_viewport {"filepath": "bearing_housing.png", "view_name": "*Isometric"}
```

The four M6 counterbores are the modeled interface. The screws themselves are not modeled. List them for
download following `solidworks-purchased-parts`, for example: 4 × ISO 4762 M6×20, 8.8, zinc plated, in the
flange counterbores, from TraceParts "ISO 4762 M6x20". If the housing carries a bearing in the Ø50 bore,
list the bearing too, with its maker part number.
