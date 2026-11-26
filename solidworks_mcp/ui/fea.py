"""
SolidWorks FEA Results - MCP App state
--------------------------------------
Backs the ui://solidworks/fea.html view. show_fea_results collects the results of a
solved SolidWorks Simulation study (von Mises stress, resultant displacement, mesh
size), a viewport image (SolidWorks keeps the result plot on screen after a solve)
and the material's yield strength, and returns them as structuredContent.fea:

    {
      "study", "model", "material", "view", "views", "image", "status",
      "stress": {"max", "min", "max_node", "unit"},
      "displacement": {"max", "min", "max_node", "unit"},
      "yield_mpa", "yield_source",       # "given" | "material" | "library" | null
      "fos",                             # yield / max stress, or null
      "mesh": {"elements", "nodes", "element_size_mm"} | null,
      "warnings": [...],
      "summary"                          # ready-made prompt for the agent
    }

The view calls the app-only fea_action tool to re-solve the study or change the view.
"""

import logging
import math
import threading
from typing import Any, Dict, List, Optional

from . import studio

logger = logging.getLogger("SolidWorksMCP.FEA")

RESOURCE_URI = "ui://solidworks/fea.html"
VIEW_NAMES = studio.VIEW_NAMES

# Yield strengths (MPa) of common SolidWorks library materials, used only when the
# study material cannot be read through the Simulation API.
LIBRARY_YIELD_MPA = {
    "plain carbon steel": 220.594,
    "aisi 1020": 351.571,
    "aisi 304": 206.807,
    "alloy steel": 620.422,
    "cast carbon steel": 248.168,
    "1060 alloy": 27.5742,
    "6061 alloy": 55.1485,
    "6061-t6 (ss)": 275.0,
    "6061-t6": 275.0,
    "7075-t6 (sn)": 505.0,
    "7075-t6": 505.0,
}

_automation_instance: Any = None
_lock = threading.Lock()


class FeaSession:
    def __init__(self) -> None:
        self.study = ""
        self.model = ""
        self.material = ""
        self.view = "*Isometric"
        self.image = ""
        self.stress: Optional[Dict[str, Any]] = None
        self.displacement: Optional[Dict[str, Any]] = None
        self.yield_mpa: Optional[float] = None
        self.yield_source: Optional[str] = None
        self.mesh: Optional[Dict[str, Any]] = None
        self.warnings: List[str] = []
        self.status = ""

    def fos(self) -> Optional[float]:
        peak = (self.stress or {}).get("max")
        if not self.yield_mpa or not peak or peak <= 0:
            return None
        return self.yield_mpa / peak

    def state(self) -> Dict[str, Any]:
        fos = self.fos()
        return {
            "study": self.study,
            "model": self.model,
            "material": self.material,
            "view": self.view,
            "views": list(VIEW_NAMES),
            "image": self.image,
            "status": self.status,
            "stress": self.stress,
            "displacement": self.displacement,
            "yield_mpa": self.yield_mpa,
            "yield_source": self.yield_source,
            "fos": round(fos, 3) if fos is not None else None,
            "mesh": self.mesh,
            "warnings": list(self.warnings),
            "summary": build_summary(self),
        }


_session = FeaSession()


def get_session() -> FeaSession:
    return _session


def set_automation_instance(instance: Any) -> None:
    global _automation_instance
    _automation_instance = instance
    studio.set_automation_instance(instance)  # viewport capture is shared with the studio


def render_page() -> str:
    from .apps import render
    return render(RESOURCE_URI)


def _fmt(value: Optional[float], digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}g}"


def build_summary(s: FeaSession) -> str:
    """Prompt text for the agent, describing the result in engineering terms."""
    if not s.stress:
        return ""
    fos = s.fos()
    lines = [
        f"FEA results for study '{s.study}' on {s.model or 'the model'}"
        + (f" ({s.material})" if s.material else "") + ":",
        f"- Max von Mises stress: {_fmt(s.stress.get('max'))} MPa",
    ]
    if s.displacement:
        lines.append(f"- Max resultant displacement: {_fmt(s.displacement.get('max'))} mm")
    if fos is not None:
        lines.append(f"- Minimum factor of safety (yield {_fmt(s.yield_mpa)} MPa / max stress): {fos:.2f}")
    for w in s.warnings:
        lines.append(f"- Warning: {w}")
    lines.append("Please review whether the design meets its requirements and suggest changes if it does not.")
    return "\n".join(lines)


# ============================================================================
# SolidWorks access
# ============================================================================

def _call(func_name: str, **kwargs: Any) -> Any:
    automation = _automation_instance
    if automation is None:
        raise RuntimeError("SolidWorks automation is not available")
    return getattr(automation, func_name)(**kwargs)


def _material_and_yield(study_name: Optional[str]) -> tuple:
    """(material name, yield MPa or None, source) read from the study's first solid body."""
    automation = _automation_instance
    try:
        ok, _msg, study = automation._get_active_study(study_name)
        if not ok or study is None:
            return "", None, None
        import pythoncom
        import win32com.client
        err = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        comp = study.SolidManager.GetComponentAt(0, err)
        body = comp.GetSolidBodyAt(0, err) if comp is not None else None
        mat = body.GetSolidBodyMaterial() if body is not None else None
        name = str(getattr(mat, "MaterialName", "") or "") if mat is not None else ""
    except Exception as e:
        logger.debug(f"FEA viewer: study material not readable: {e}")
        return "", None, None

    # ICWMaterial.GetPropertyByName returns SI values (N/m^2) for SIGYLD.
    try:
        import pythoncom
        import win32com.client
        err = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        raw = mat.GetPropertyByName("SIGYLD", "", err)
        if isinstance(raw, tuple):
            raw = raw[0]
        value = float(raw) / 1e6
        if 1.0 <= value <= 5000.0:
            return name, value, "material"
    except Exception as e:
        logger.debug(f"FEA viewer: SIGYLD not readable: {e}")

    library = LIBRARY_YIELD_MPA.get(name.strip().lower())
    return name, library, ("library" if library else None)


def _model_title() -> str:
    try:
        doc, err = _automation_instance.get_active_doc()
        if not err and doc is not None:
            title = getattr(doc, "GetTitle", "")
            return str(title() if callable(title) else title)
    except Exception:
        pass
    return ""


def _apply_results(res: Dict[str, Any]) -> bool:
    if not isinstance(res, dict) or not res.get("success"):
        return False
    data = res.get("data") or {}
    _session.study = data.get("study_name") or _session.study
    _session.stress = data.get("von_mises_stress_mpa")
    _session.displacement = data.get("resultant_displacement_mm")
    _session.warnings = list((data.get("sanity_check") or {}).get("warnings") or [])
    return True


def load(study_name: Optional[str] = None, yield_mpa: Optional[float] = None,
         view: str = "*Isometric") -> Dict[str, Any]:
    """Collect a solved study's results. Returns the tool result dict (success, message, data)."""
    with _lock:
        res = _call("get_simulation_results", study_name=study_name)
        if not _apply_results(res):
            message = res.get("message") if isinstance(res, dict) else "No simulation results."
            return {"success": False, "message": message or "No simulation results.", "data": None}

        _session.model = _model_title()
        _session.view = view if view in VIEW_NAMES else "*Isometric"
        _session.mesh = None
        material, lib_yield, source = _material_and_yield(study_name)
        _session.material = material
        if _valid_yield(yield_mpa):
            _session.yield_mpa, _session.yield_source = float(yield_mpa), "given"
        else:
            _session.yield_mpa, _session.yield_source = lib_yield, source
        _session.image = studio.capture_viewport_data_uri(_session.view)
        _session.status = "Results loaded."
        return {"success": True, "message": "", "data": _session.state()}


def _valid_yield(value: Any) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0


def handle_action(action: str, view: Any = None, element_size_mm: Any = None,
                  yield_mpa: Any = None) -> Dict[str, Any]:
    """Run one viewer action (rerun, capture, set_yield) and return the new state."""
    with _lock:
        if view in VIEW_NAMES:
            _session.view = view
        if action == "rerun":
            size = element_size_mm
            try:
                size = float(size) if size is not None else None
            except (TypeError, ValueError):
                size = None
            if size is None or not math.isfinite(size) or size <= 0:
                size = (_session.mesh or {}).get("element_size_mm") or 2.0
            run = _call("mesh_and_run_simulation", element_size_mm=size, study_name=_session.study or None)
            if not isinstance(run, dict) or not run.get("success"):
                reason = run.get("message", "solver failed") if isinstance(run, dict) else "solver failed"
                _session.status = f"Re-solve failed: {reason}"
            else:
                data = run.get("data") or {}
                _session.mesh = {
                    "elements": data.get("element_count"),
                    "nodes": data.get("node_count"),
                    "element_size_mm": data.get("element_size_mm", size),
                }
                if _apply_results(_call("get_simulation_results", study_name=_session.study or None)):
                    _session.status = f"Re-solved with {size:g} mm elements."
                else:
                    _session.status = "Solved, but the results could not be read."
                _session.image = studio.capture_viewport_data_uri(_session.view) or _session.image
        elif action == "capture":
            _session.image = studio.capture_viewport_data_uri(_session.view) or _session.image
            _session.status = f"Showing {_session.view.lstrip('*')} view."
        elif action == "set_yield":
            if _valid_yield(yield_mpa):
                _session.yield_mpa, _session.yield_source = float(yield_mpa), "given"
                _session.status = f"Yield strength set to {float(yield_mpa):g} MPa."
            else:
                _session.status = "Yield strength must be a positive number."
        else:
            _session.status = f"Unknown FEA action '{action}'."
        return _session.state()
