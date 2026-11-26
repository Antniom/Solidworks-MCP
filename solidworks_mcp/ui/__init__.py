"""Interactive studio: an MCP App view (studio.html) and the state behind it (studio.py)."""

from .studio import (
    get_session,
    handle_action,
    render_page,
    set_automation_instance,
    set_session,
)

__all__ = [
    "get_session",
    "handle_action",
    "render_page",
    "set_automation_instance",
    "set_session",
]
