"""
SolidWorks MCP - Interactive UI & Fast-Bridge Package
----------------------------------------------------
Provides fast-bridge HTTP daemon, responsive CAD studio template,
and parametric dimension slider generator.
"""

from .bridge import (
    start_bridge,
    stop_bridge,
    get_bridge_port,
    is_bridge_running,
    set_automation_instance,
    set_current_app_html,
    FastBridgeServer,
)

__all__ = [
    "start_bridge",
    "stop_bridge",
    "get_bridge_port",
    "is_bridge_running",
    "set_automation_instance",
    "set_current_app_html",
    "FastBridgeServer",
]
