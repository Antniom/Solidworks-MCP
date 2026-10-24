"""
Standalone Fast-Bridge Daemon runner for SolidWorks Interactive Studio.
Connects to active SolidWorks instance, serves live dimension updates,
and synchronizes in-chat widgets with external browser windows.
"""

import sys
import time
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FastBridgeDaemon")

def run():
    from solidworks_mcp.automation.base import SolidWorksAutomation
    from solidworks_mcp.ui.bridge import start_bridge, set_automation_instance, set_current_app_html
    from solidworks_mcp.ui.generator import generate_studio_html

    auto = SolidWorksAutomation()
    conn_res = auto.connect()
    logger.info(f"Connected to SolidWorks: {conn_res.get('success', False)}")
    set_automation_instance(auto)

    # Cache latest artifact HTML
    artifact_path = Path(r"C:\Users\anton\.gemini\antigravity\brain\c35df9a4-9ee8-4d1d-a94d-7590bf0b0072\solidworks_studio.html")
    if artifact_path.exists():
        html_content = artifact_path.read_text(encoding="utf-8")
        set_current_app_html(html_content)
        logger.info(f"Loaded {len(html_content)} bytes of studio HTML into bridge cache")

    port = start_bridge(54321)
    logger.info(f"FastBridge daemon running on port {port}")

    while True:
        time.sleep(3600)

if __name__ == "__main__":
    run()
