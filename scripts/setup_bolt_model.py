"""
Sets up the M12x50 bolt in SolidWorks with cleanly renamed features:
'Hex-Head' and 'Bolt-Shaft', and starts Fast-Bridge.
"""

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from solidworks_mcp.automation import SolidWorksAutomation
from solidworks_mcp.server import _show_interactive_studio_handler
from solidworks_mcp.ui.bridge import start_bridge, get_bridge_port, set_automation_instance, is_bridge_running

def main():
    print("Connecting to SolidWorks...")
    sw = SolidWorksAutomation()
    conn = sw.connect()
    print("Connection:", conn.get("message"))
    if not conn.get("success"):
        sys.exit(1)

    print("Creating bolt part...")
    sw.create_new_part()

    # Hex Head
    sw.create_sketch(plane="Front")
    sw.draw_polygon(cx=0, cy=0, radius=10.392, sides=6, unit="mm")
    sw.extrude_sketch(depth=4.8, unit="mm")
    sw.manage_feature(action="rename", feature_name="Boss-Extrude1", new_name="Hex-Head")
    print("Created Hex-Head (4.8mm)")

    # Bolt Shaft
    sw.create_sketch_on_face(x=0, y=0, z=4.8, unit="mm")
    sw.draw_circle(x=0, y=0, radius=6.0, unit="mm")
    sw.extrude_sketch(depth=50.0, unit="mm")
    # In SolidWorks, the second extrusion might be Boss-Extrude1 (since the first was renamed) or Boss-Extrude2
    feats_data = sw.list_features().get("data", {})
    feats = feats_data.get("features", []) if isinstance(feats_data, dict) else (feats_data if isinstance(feats_data, list) else [])
    for f in feats:
        fname = f.get("name", "") if isinstance(f, dict) else str(f)
        if "Extrude" in fname and fname != "Hex-Head":
            sw.manage_feature(action="rename", feature_name=fname, new_name="Bolt-Shaft")
            print(f"Renamed {fname} -> Bolt-Shaft")
            break

    set_automation_instance(sw)
    print("Launching interactive studio handler...")
    res = _show_interactive_studio_handler({
        "view_name": "*Isometric",
        "feature_labels": {
            "Hex-Head": "Hex Bolt Head",
            "Bolt-Shaft": "Bolt Shaft"
        },
        "dimension_labels": {
            "D1@Hex-Head": "Head Thickness",
            "D1@Bolt-Shaft": "Shaft Length"
        }
    })
    port = res.get("data", {}).get("bridgePort")
    print("Studio launched:", res.get("success"))
    print("Artifact URL:", res.get("data", {}).get("artifactUrl"))
    print("Bridge Port:", port)
    print("Fast-Bridge running:", is_bridge_running())

    if is_bridge_running():
        print(f"Fast-Bridge daemon active on port {port}. Keeping process alive...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Stopping Fast-Bridge daemon...")

if __name__ == "__main__":
    main()
