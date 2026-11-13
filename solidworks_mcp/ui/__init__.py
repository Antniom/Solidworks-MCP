"""Interactive studio: A2UI surface builder and the local bridge that serves it."""

from .bridge import (
    start_bridge,
    stop_bridge,
    get_bridge_port,
    get_studio_url,
    is_bridge_running,
    set_automation_instance,
    set_session,
    render_page,
)

__all__ = [
    "start_bridge",
    "stop_bridge",
    "get_bridge_port",
    "get_studio_url",
    "is_bridge_running",
    "set_automation_instance",
    "set_session",
    "render_page",
]
