"""
MCP Apps views served by the connector (MCP Apps extension, SEP-1865).

Each view is one HTML file in this folder with no external assets. The shared host
bridge (bridge.js) is inlined at the /*__MCP_APP_BRIDGE__*/ marker when the page is served.
"""

from pathlib import Path
from typing import Dict

UI_DIR = Path(__file__).parent
MIME_TYPE = "text/html;profile=mcp-app"
BRIDGE_MARKER = "/*__MCP_APP_BRIDGE__*/"

# ui:// resource URI -> (file name, resource name, description)
VIEWS: Dict[str, tuple] = {
    "ui://solidworks/studio.html": (
        "studio.html",
        "SolidWorks Interactive Studio",
        "Live dimension sliders and a SolidWorks viewport preview.",
    ),
    "ui://solidworks/fea.html": (
        "fea.html",
        "SolidWorks FEA Results",
        "Stress, displacement and factor of safety of a solved Simulation study, with a load what-if and re-solve.",
    ),
    "ui://solidworks/motion.html": (
        "motion.html",
        "SolidWorks Motion Scrubber",
        "Drag an assembly mate through its range with a live viewport and a collision map.",
    ),
    "ui://solidworks/project.html": (
        "project.html",
        "SolidWorks Project Board",
        "The TRACKLIST of a solidworks-project folder as a board, with open questions and decisions.",
    ),
}


def render(uri: str) -> str:
    """The self-contained HTML for a view URI. Raises KeyError for an unknown URI."""
    file_name = VIEWS[uri][0]
    page = (UI_DIR / file_name).read_text(encoding="utf-8")
    bridge = (UI_DIR / "bridge.js").read_text(encoding="utf-8")
    if BRIDGE_MARKER not in page:
        raise ValueError(f"{file_name} has no {BRIDGE_MARKER} marker")
    return page.replace(BRIDGE_MARKER, bridge.replace("</script", "<\\/script"))
