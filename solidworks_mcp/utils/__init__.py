"""
SolidWorks MCP Utilities
------------------------
Utility modules for unit conversion, SolidWorks detection, and validation.
"""

from .units import (
    Unit,
    UnitConverter,
    mm, cm, inch, ft,
    to_mm, to_inch,
    get_converter,
    get_unit_converter,
    set_default_unit,
)

from .sw_finder import (
    SolidWorksFinder,
    find_solidworks,
    find_template,
    get_solidworks_info,
    resolve_solidworks_install_dir,
    resolve_bom_template,
    resolve_simulation_dll,
    resolve_materials_db,
)

from .dialog_manager import (
    get_sldworks_pids,
    get_all_solidworks_windows,
    list_active_dialogs,
    get_main_solidworks_hwnd,
    find_blocking_modal_dialog,
    dismiss_dialog,
    auto_resolve_blocking_dialogs,
    DialogWatchdog,
)

from .auto_updater import check_and_apply_updates

__all__ = [
    # Auto-Updater
    "check_and_apply_updates",
    # Units
    "Unit",
    "UnitConverter", 
    "mm", "cm", "inch", "ft",
    "to_mm", "to_inch",
    "get_converter",
    "get_unit_converter",
    "set_default_unit",
    
    # SolidWorks Finder
    "SolidWorksFinder",
    "find_solidworks",
    "find_template",
    "get_solidworks_info",
    "resolve_solidworks_install_dir",
    "resolve_bom_template",
    "resolve_simulation_dll",
    "resolve_materials_db",

    # Dialog Manager
    "get_sldworks_pids",
    "get_all_solidworks_windows",
    "list_active_dialogs",
    "get_main_solidworks_hwnd",
    "find_blocking_modal_dialog",
    "dismiss_dialog",
    "auto_resolve_blocking_dialogs",
    "DialogWatchdog",
]
