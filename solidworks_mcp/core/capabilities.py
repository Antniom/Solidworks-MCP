"""
SolidWorks Capabilities Inspector
---------------------------------
Discovers and reports runtime capabilities: SOLIDWORKS version, build,
installed add-ins, active session health, STA worker state, and active document diagnostics.
"""

import os
from typing import Any, Dict, List, Optional

from .com_worker import get_sta_worker
from ..utils import get_solidworks_info


class CapabilityInspector:
    """Discovers and formats SolidWorks system capabilities and runtime state."""

    # Candidate ProgIDs per add-in (first registered one wins). The previous single
    # ProgIDs (e.g. "SldWorks.SimulationAddin") do not exist, so every add-in showed
    # as not loaded even while Simulation was running studies.
    ADDINS_TO_CHECK = {
        "simulation": {
            "prog_ids": ["CosmosWorks.CosmosWorks", "SldWorks.Simulation"],
            "name": "SOLIDWORKS Simulation (COSMOSWorks)",
        },
        "photoview": {
            "prog_ids": ["PhotoView360.PhotoView360Addin", "PhotoView360.Addin"],
            "name": "PhotoView 360",
        },
        "toolbox": {
            "prog_ids": ["swBrowser.Browser", "SWBrowser.SWBrowserAddIn", "Toolbox.Addin"],
            "name": "SOLIDWORKS Toolbox",
        },
        "routing": {
            "prog_ids": ["Routing.RoutingAddIn", "Routing.Addin"],
            "name": "SOLIDWORKS Routing",
        },
        "motion": {
            "prog_ids": ["SWMotion.SWMotionAddIn", "Motion.Addin"],
            "name": "SOLIDWORKS Motion",
        },
    }

    @staticmethod
    def _progid_registered(prog_id: str) -> bool:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"{prog_id}\\CLSID"):
                return True
        except Exception:
            return False

    @classmethod
    def get_system_capabilities(cls, sw_app: Optional[Any] = None) -> Dict[str, Any]:
        """
        Inspect the local system and SolidWorks instance for supported features.
        """
        info = get_solidworks_info()
        sta = get_sta_worker()

        caps: Dict[str, Any] = {
            "installation": {
                "found": info.get("found", False),
                "version": info.get("version"),
                "exe_path": info.get("exe_path"),
                "install_dir": info.get("install_dir"),
                "templates": info.get("templates", {}),
            },
            "runtime": {
                "connected": sw_app is not None,
                "sta_worker_alive": sta.is_alive,
                "sta_thread_id": sta.thread_id,
            },
            "addins": {},
            "active_document": None,
        }

        # Query live COM application if available
        if sw_app is not None:
            try:
                # Query revision number
                try:
                    rev = sw_app.RevisionNumber
                except Exception:
                    rev = sw_app.RevisionNumber()
                caps["runtime"]["revision"] = str(rev)

                # Query document count
                try:
                    raw_count = getattr(sw_app, "GetDocumentCount", None)
                    count_val = raw_count() if callable(raw_count) else raw_count
                    caps["runtime"]["document_count"] = int(count_val) if count_val is not None else 0
                except Exception:
                    pass

                # Active document info
                doc = getattr(sw_app, "ActiveDoc", None)
                if doc is not None:
                    try:
                        title = doc.GetTitle
                        if callable(title):
                            title = title()
                    except Exception:
                        title = "Unknown"

                    try:
                        path = doc.GetPathName
                        if callable(path):
                            path = path()
                    except Exception:
                        path = ""

                    try:
                        doc_type = doc.GetType
                        if callable(doc_type):
                            doc_type = doc_type()
                        type_str = {1: "Part", 2: "Assembly", 3: "Drawing"}.get(doc_type, "Unknown")
                    except Exception:
                        type_str = "Unknown"

                    caps["active_document"] = {
                        "title": title,
                        "path": path,
                        "type": type_str,
                    }

                # Check add-ins
                for addin_key, addin_meta in cls.ADDINS_TO_CHECK.items():
                    registered = [p for p in addin_meta["prog_ids"] if cls._progid_registered(p)]
                    entry = {
                        "name": addin_meta["name"],
                        "installed": bool(registered),
                        "loaded": False,
                    }
                    for prog_id in registered:
                        try:
                            # GetAddInObject returns the add-in object only while it is loaded
                            if sw_app.GetAddInObject(prog_id) is not None:
                                entry["loaded"] = True
                                entry["prog_id"] = prog_id
                                break
                        except Exception:
                            continue
                    caps["addins"][addin_key] = entry
            except Exception as e:
                caps["runtime"]["inspection_error"] = str(e)

        return caps
