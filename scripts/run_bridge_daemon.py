"""
Standalone Fast-Bridge Daemon runner for SolidWorks Interactive Studio.
Connects to active SolidWorks instance, serves live dimension updates,
and synchronizes in-chat widgets with external browser windows.
"""

import os
import sys
import time
import base64
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FastBridgeDaemon")

def run():
    from solidworks_mcp.automation import SolidWorksAutomation
    from solidworks_mcp.ui.bridge import start_bridge, set_automation_instance, set_current_app_html
    from solidworks_mcp.ui.generator import generate_studio_html

    auto = SolidWorksAutomation()
    conn_res = auto.connect()
    logger.info(f"Connected to SolidWorks: {conn_res.get('success', False)}")
    set_automation_instance(auto)

    # Ensure active model exists
    doc, _ = auto.get_active_doc()
    if not doc:
        logger.info("No active document found in SolidWorks. Modeling demo Hex Bolt...")
        auto.create_new_part()
        auto.create_sketch(plane="Top")
        auto.draw_polygon(cx=0, cy=0, radius=25, sides=6)
        auto.extrude_sketch(depth=20)
        auto.manage_feature(action="rename", feature_name="Boss-Extrude1", new_name="Hex-Head")
        auto.create_sketch(plane="Top")
        auto.draw_circle(x=0, y=0, radius=10)
        auto.extrude_sketch(depth=80, reverse_dir=True)
        auto.manage_feature(action="rename", feature_name="Boss-Extrude2", new_name="Bolt-Shaft")

    # Generate fresh studio HTML from live model
    dims_res = auto.get_dimensions()
    dims = []
    if isinstance(dims_res, dict):
        d_val = dims_res.get("data")
        if isinstance(d_val, list):
            dims = d_val
        elif isinstance(d_val, dict):
            dims = d_val.get("dimensions", [])
        elif isinstance(dims_res.get("dimensions"), list):
            dims = dims_res.get("dimensions", [])

    feature_labels = {"Hex-Head": "Hex Bolt Head", "Bolt-Shaft": "Bolt Shaft"}
    dimension_labels = {
        "D1@Hex-Head": "Head Thickness",
        "D1@Bolt-Shaft": "Shaft Length",
    }
    cap_res = auto.capture_viewport(view_name="*Isometric")
    img_b64 = ""
    if isinstance(cap_res, dict) and cap_res.get("success"):
        fp = cap_res.get("data", {}).get("filepath")
        if fp and os.path.exists(fp):
            with open(fp, "rb") as f:
                img_b64 = f"data:image/png;base64,{base64.b64encode(f.read()).decode('ascii')}"

    insp_res = auto.inspect_model()
    bbox_info = insp_res.get("data", {}).get("dimensions_mm") if isinstance(insp_res, dict) else None

    artifact_path = Path(r"C:\Users\anton\.gemini\antigravity\brain\c35df9a4-9ee8-4d1d-a94d-7590bf0b0072\solidworks_studio.html")
    html_content = generate_studio_html(
        model_title="Part1",
        dimensions=dims,
        units="mm",
        initial_image_base64=img_b64,
        bbox=bbox_info,
        bridge_port=54321,
        feature_labels=feature_labels,
        dimension_labels=dimension_labels,
        output_path=str(artifact_path),
    )
    set_current_app_html(html_content)
    logger.info(f"Loaded {len(html_content)} bytes of studio HTML into bridge cache")

    port = start_bridge(54321)
    logger.info(f"FastBridge daemon running on port {port}")

    while True:
        time.sleep(3600)

if __name__ == "__main__":
    run()
