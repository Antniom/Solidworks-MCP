"""
End-to-End User Simulation Test:
1. Connect to SolidWorks
2. Create a 3D Part (50x30mm rectangle extruded 20mm)
3. Open Interactive Studio (generates artifact, starts Fast-Bridge on 127.0.0.1:54321)
4. Simulate user dragging slider via Fast-Bridge HTTP POST /api/set_dimension
5. Verify SolidWorks model rebuilds to new dimension and returns updated viewport render
"""

import sys
import os
import json
import urllib.request
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solidworks_mcp.server import sw_automation, _show_interactive_studio_handler
from solidworks_mcp.ui.bridge import get_bridge_port, is_bridge_running

print("=" * 70)
print(" 1. CONNECTING TO SOLIDWORKS")
print("=" * 70)
conn_res = sw_automation.connect()
print("Connect result:", conn_res.get("message"))
if not conn_res.get("success"):
    print("FATAL: Could not connect to SolidWorks:", conn_res)
    sys.exit(1)

print("\n" + "=" * 70)
print(" 2. CREATING 3D PART (50x30x20mm)")
print("=" * 70)
part_res = sw_automation.create_new_part()
print("Part created:", part_res.get("message"))

sketch_res = sw_automation.create_sketch(plane="Front")
print("Sketch created on Front Plane:", sketch_res.get("message"))

rect_res = sw_automation.draw_rectangle(x1=0, y1=0, x2=50, y2=30, unit="mm")
print("Rectangle drawn (50x30mm):", rect_res.get("message"))

extrude_res = sw_automation.extrude_sketch(depth=20.0, unit="mm")
print("Extrude boss (20mm):", extrude_res.get("message"))

# Verify initial dimensions
dims = sw_automation.get_dimensions()
dims_data = dims.get("data", {}) if isinstance(dims, dict) else {}
dim_list = dims_data.get("dimensions", []) if isinstance(dims_data, dict) else (dims_data if isinstance(dims_data, list) else [])
print(f"Dimensions detected ({len(dim_list)}):")
for d in dim_list:
    print(f"  - {d['full_name']}: {d['value']} {d['units']} ({d['owner_feature']})")

print("\n" + "=" * 70)
print(" 3. OPENING INTERACTIVE STUDIO (show_interactive_studio)")
print("=" * 70)
studio_res = _show_interactive_studio_handler({"view_name": "*Isometric"})
print("Studio Success:", studio_res.get("success"))
data = studio_res.get("data", {})
port = data.get("bridgePort")
artifact_path = data.get("artifactPath")
print(f"Fast-Bridge Port: {port}")
print(f"Studio Artifact Path: {artifact_path}")
print(f"Fast-Bridge Running: {is_bridge_running()}")

print("\n" + "=" * 70)
print(" 4. SIMULATING USER SLIDER DRAG VIA FAST-BRIDGE HTTP")
print("=" * 70)
# Find primary linear dimension to adjust (e.g. D1@Sketch1 or D1@Boss-Extrude1)
dim_list = data.get("dimensions", [])
target_dim = dim_list[0] if dim_list else None
if not target_dim:
    print("No dimensions found to adjust!")
    sys.exit(1)

target_name = target_dim["full_name"]
orig_val = target_dim["value"]
new_val = round(orig_val * 1.5, 2)

print(f"Adjusting '{target_name}' from {orig_val} -> {new_val} {target_dim['units']} via HTTP POST...")

url = f"http://127.0.0.1:{port}/api/set_dimension"
payload = json.dumps({
    "name": target_name,
    "value": new_val,
    "units": target_dim["units"],
    "view_name": "*Isometric"
}).encode("utf-8")

req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        result_data = json.loads(resp.read().decode("utf-8"))
except urllib.error.HTTPError as he:
    err_body = he.read().decode("utf-8")
    print(f"HTTP Error {he.code}: {err_body}")
    sys.exit(1)

print("Fast-Bridge HTTP Response:")
print(f"  - Success: {result_data.get('success')}")
print(f"  - Value readback: {result_data.get('value')} {result_data.get('units')}")
print(f"  - Viewport image returned: {bool(result_data.get('image_base64'))} (length: {len(result_data.get('image_base64') or '')} chars)")
print(f"  - Updated bounds: {result_data.get('bbox')}")

print("\n" + "=" * 70)
print(" 5. VERIFYING ACTUAL SOLIDWORKS MODEL STATE")
print("=" * 70)
recheck_dims = sw_automation.get_dimensions(dimension_name=target_name)
recheck_data = recheck_dims.get("data", {}) if isinstance(recheck_dims, dict) else {}
items = recheck_data.get("dimensions", []) if isinstance(recheck_data, dict) else (recheck_data if isinstance(recheck_data, list) else [])
actual_val = items[0]["value"] if items else None
print(f"Actual dimension value inside SolidWorks COM: {actual_val}")
assert actual_val == new_val, f"Mismatch: expected {new_val}, got {actual_val}"

print("\n>>> ALL USER FLOW CHECKS PASSED LIVE IN SOLIDWORKS! <<<")
