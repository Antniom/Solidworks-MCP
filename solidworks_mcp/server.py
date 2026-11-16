"""
SolidWorks MCP Server
---------------------
MCP entry point: routes tool calls to SolidWorksAutomation on the STA COM worker.

Tool schemas live in tool_schemas.py; this module holds the dispatch table,
the handful of tools that need custom glue, and the MCP request handlers.
"""

import os
import io
import sys
import asyncio
import threading
import json
import logging
import tempfile
import traceback
from typing import Any, Callable, Dict, List, Tuple, Optional
from pathlib import Path

from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Resource,
    ListResourcesResult,
    ReadResourceResult,
    ReadResourceRequestParams,
    TextResourceContents,
)

import time
import uuid
import datetime

from . import __version__
from .automation import SolidWorksAutomation
from .constants import SwErrors
from .config import get_config
from .tool_schemas import tool_definitions, validate_tool_arguments, length_unit_arguments
from .utils import get_solidworks_info
from .core import (
    create_envelope,
    error_envelope,
    get_sta_worker,
    SolidWorksModalBlockedException,
    AbandonedCallRunningException,
    CapabilityInspector,
    get_knowledge_manager,
    KnowledgeRecord,
    PromotionState,
    get_checkpoint_manager,
    get_attr_or_call,
    get_object_member,
    find_blocking_modal_dialog,
    get_mecagent_runner,
    capture_vol_state,
    format_vol_delta,
)

STUDIO_RESOURCE_URI = "ui://solidworks/studio.html"
STUDIO_MIME_TYPE = "text/html;profile=mcp-app"


def _configure_logging() -> Optional[Path]:
    """Log to a file under the user's local app data (stderr as a last resort)."""
    cfg = get_config()
    # Never write into the install/plugin folder: it may be read-only and is replaced on update.
    candidates = [
        Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "solidworks-mcp" / cfg.log_file,
        Path(tempfile.gettempdir()) / "solidworks-mcp" / cfg.log_file,
    ]
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = logging.FileHandler(path, encoding="utf-8")
        except OSError:
            continue
        logging.basicConfig(
            level=cfg.get_log_level_int(),
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[handler],
        )
        return path
    # stdout carries the MCP protocol, so the only safe fallback is stderr.
    logging.basicConfig(level=cfg.get_log_level_int(), stream=sys.stderr)
    return None


LOG_FILE = _configure_logging()
logger = logging.getLogger("SolidWorksMCP")

sw_automation = SolidWorksAutomation()


def _tool_definitions():
    """Kept for callers that imported the catalog from this module."""
    return tool_definitions()


async def list_tools(
    ctx: Optional[ServerRequestContext] = None,
    params: Optional[PaginatedRequestParams] = None,
) -> ListToolsResult:
    return ListToolsResult(tools=tool_definitions())


def format_result(r: Dict) -> str:
    """Format a result dictionary as readable text."""
    status = "SUCCESS" if r["success"] else "ERROR"
    lines = [f"[{status}] {r['message']}"]
    if not r["success"]:
        lines.append(f"Error Code: {r['error_code']} ({r['error_name']})")
    if r.get("data"):
        lines.append("Details: " + json.dumps(r["data"], indent=2))
    return "\n".join(lines)


# Tools that were merged: old name -> how to call the replacement.
_MERGED_TOOLS: Dict[str, str] = {
    "get_solidworks_info": "get_capabilities (see data.installation)",
    "create_new_part": 'create_document {"type": "part"}',
    "create_new_assembly": 'create_document {"type": "assembly"}',
    "create_new_drawing": 'create_document {"type": "drawing", "paper_size": ...}',
    "create_sketch_on_face": 'create_sketch {"face_point": [x, y, z]}',
    "list_features": 'get_feature_tree {"detail": "list"}',
    "sw_get_feature_tree_json": 'get_feature_tree {"detail": "parametric"}',
    "sw_get_context_fast": 'get_feature_tree {"detail": "context"}',
    "select_entity": 'select {"by": "name"} or select {"by": "point", "point": [x, y, z]}',
    "select_by_geometry": 'select {"by": "geometry", ...}',
    "find_face": 'select {"by": "centroid", "target_type": "face", ...}',
    "find_edge": 'select {"by": "centroid", "target_type": "edge", ...}',
    "sw_select_by_path": 'select {"by": "path", "path": ...}',
    "list_faces": 'list_topology {"kind": "faces"}',
    "list_edges": 'list_topology {"kind": "edges"}',
    "sw_navigate_topology": "inspect_topology",
    "sw_get_edge_analysis": 'inspect_topology {"path": ..., "edges": true}',
    "sw_capture_views": 'capture_viewport {"mode": "overview"}',
    "render_showcase_viewport": 'capture_viewport {"mode": "showcase", ...}',
    "create_checkpoint": 'transaction {"action": "checkpoint", "name": ...}',
    "rollback_checkpoint": 'transaction {"action": "rollback", "name": ...}',
    "execute_python": 'execute_code {"language": "python", ...}',
    "sw_execute_csharp": 'execute_code {"language": "csharp", ...}',
    "sw_execute_macro": 'execute_code {"language": "csharp_macro", ...}',
    "get_parameters": 'manage_parameters {"action": "list"}',
    "add_parameter": 'manage_parameters {"action": "add", ...}',
    "set_parameter": 'manage_parameters {"action": "set", ...}',
    "sw_modify_feature_parameter_by_path": "modify_feature_by_path",
    "sw_find_related_files": "find_related_files",
}


def _validate_tool_arguments(name: str, arguments: Dict[str, Any]) -> Optional[str]:
    if name in _MERGED_TOOLS:
        return f"Tool '{name}' was merged. Use: {_MERGED_TOOLS[name]}"
    return validate_tool_arguments(name, arguments)


# ============================================================================
# Dispatch table
# ============================================================================

_MISSING = object()


def _auto(method: str, *params: Any) -> Callable[[dict], Dict[str, Any]]:
    """
    Build a handler that forwards tool arguments to a SolidWorksAutomation method.

    Each param is one of:
        "key"                     -> method(key=arguments.get("key"))
        ("key", default)          -> method(key=arguments.get("key", default))
        ("kwarg", "key", default) -> method(kwarg=arguments.get("key", default))
    """
    specs = []
    for p in params:
        if isinstance(p, str):
            specs.append((p, p, None))
        elif len(p) == 2:
            specs.append((p[0], p[0], p[1]))
        else:
            specs.append(tuple(p))

    def handler(arguments: dict) -> Dict[str, Any]:
        kwargs = {kwarg: arguments.get(key, default) for kwarg, key, default in specs}
        return getattr(sw_automation, method)(**kwargs)

    return handler


def _ok(message: str, data: Any = None) -> Dict[str, Any]:
    return {"success": True, "message": message, "error_code": 0, "error_name": "swSuccess", "data": data}


def _runner_result(res: Dict[str, Any], ok_message: Optional[str], fail_message: str) -> Dict[str, Any]:
    """Wrap a MecAgent runner response in the standard result shape."""
    ok = bool(res.get("success", False))
    if ok:
        message = res.get("message") or res.get("details") or res.get("summary") or ok_message or "Done"
    else:
        message = res.get("message") or res.get("errorMessage") or res.get("error") or fail_message
    return {
        "success": ok,
        "message": message,
        "error_code": 0 if ok else int(SwErrors.swGeneralError),
        "error_name": "swSuccess" if ok else "swGeneralError",
        "data": res,
    }


def _sdk(sdk_method: str, fail_message: str, ok_message: Optional[str] = None,
         build: Callable[[dict], Dict[str, Any]] = lambda a: {}) -> Callable[[dict], Dict[str, Any]]:
    """Handler for a read/inspect call on the MecAgent C# SDK bundle."""
    def handler(arguments: dict) -> Dict[str, Any]:
        res = get_mecagent_runner().invoke_sdk_method(sdk_method, build(arguments))
        return _runner_result(res, ok_message, fail_message)
    return handler


def _route(key: str, routes: Dict[str, Callable[[dict], Dict[str, Any]]],
           default: Optional[str] = None) -> Callable[[dict], Dict[str, Any]]:
    """Handler for a merged tool: the value of `key` picks which operation runs."""
    def handler(arguments: dict) -> Dict[str, Any]:
        target = routes.get(arguments.get(key, default))
        if target is None:
            return sw_automation._result(
                False, f"'{key}' must be one of: {', '.join(routes)}", SwErrors.swInvalidInput)
        return target(arguments)
    return handler


def _needs(*required: str):
    """Wrap a handler so it fails cleanly when arguments its mode requires are missing."""
    def wrap(handler: Callable[[dict], Dict[str, Any]]) -> Callable[[dict], Dict[str, Any]]:
        def checked(arguments: dict) -> Dict[str, Any]:
            missing = [k for k in required if arguments.get(k) in (None, "")]
            if missing:
                return sw_automation._result(
                    False, f"Missing required argument(s): {', '.join(missing)}", SwErrors.swInvalidInput)
            return handler(arguments)
        return checked
    return wrap


def _get_capabilities(arguments: dict) -> Dict[str, Any]:
    caps = CapabilityInspector.get_system_capabilities(sw_automation.app)
    if not isinstance(caps, dict):
        caps = {"capabilities": caps}
    caps["installation"] = get_solidworks_info()
    return _ok("Capabilities discovered successfully", caps)


def _create_sketch(arguments: dict) -> Dict[str, Any]:
    point = arguments.get("face_point")
    if point is not None:
        return sw_automation.create_sketch_on_face(x=point[0], y=point[1], z=point[2], unit=arguments.get("unit"))
    return sw_automation.create_sketch(
        plane=arguments.get("plane", "Front"),
        offset=arguments.get("offset", 0.0),
        face_index=arguments.get("face_index"),
        unit=arguments.get("unit"),
    )


def _world_draw(build: Callable[[dict, Callable[[str], Optional[Tuple[float, float]]]], Dict[str, Any]]):
    """
    Wrap a draw handler so [x, y, z] world/model points (start, end, center, corner1, ...) are
    converted to the active sketch's local x/y. A point off the sketch plane is an error.
    """
    def handler(arguments: dict) -> Dict[str, Any]:
        def local(key: str) -> Optional[Tuple[float, float]]:
            pt = arguments.get(key)
            if pt is None:
                return None
            return sw_automation.world_point_to_sketch(pt, arguments.get("unit"))
        try:
            return build(arguments, local)
        except ValueError as exc:
            return sw_automation._result(False, str(exc), SwErrors.swInvalidInput)
    return handler


def _draw_line(a: dict, local) -> Dict[str, Any]:
    s, e = local("start"), local("end")
    if (s is None) != (e is None):
        raise ValueError("draw_line: give both 'start' and 'end' world points, or neither.")
    x1, y1 = s if s else (a.get("x1", 0), a.get("y1", 0))
    x2, y2 = e if e else (a.get("x2", 100), a.get("y2", 0))
    return sw_automation.draw_line(x1=x1, y1=y1, x2=x2, y2=y2, unit=a.get("unit"),
                                   construction=a.get("construction", False))


def _draw_circle(a: dict, local) -> Dict[str, Any]:
    c = local("center")
    x, y = c if c else (a.get("x", 0), a.get("y", 0))
    return sw_automation.draw_circle(x=x, y=y, radius=a.get("radius", 25), unit=a.get("unit"))


def _draw_rectangle(a: dict, local) -> Dict[str, Any]:
    c1, c2 = local("corner1"), local("corner2")
    if (c1 is None) != (c2 is None):
        raise ValueError("draw_rectangle: give both 'corner1' and 'corner2' world points, or neither.")
    x1, y1 = c1 if c1 else (a.get("x1", -50), a.get("y1", -25))
    x2, y2 = c2 if c2 else (a.get("x2", 50), a.get("y2", 25))
    return sw_automation.draw_rectangle(x1=x1, y1=y1, x2=x2, y2=y2, unit=a.get("unit"))


def _draw_arc(a: dict, local) -> Dict[str, Any]:
    pts = [local(k) for k in ("start", "mid", "end")]
    if any(p is not None for p in pts):
        if any(p is None for p in pts):
            raise ValueError("draw_arc: the 3-point form needs 'start', 'mid' and 'end' world points.")
        return sw_automation.draw_arc_3pt(start=pts[0], mid=pts[1], end=pts[2], unit=a.get("unit"))
    c = local("center")
    cx, cy = c if c else (a.get("cx", 0), a.get("cy", 0))
    return sw_automation.draw_arc_center(cx=cx, cy=cy, radius=a.get("radius", 25),
                                         start_angle=a.get("start_angle", 0), end_angle=a.get("end_angle", 90),
                                         unit=a.get("unit"))


def _draw_polygon(a: dict, local) -> Dict[str, Any]:
    c = local("center")
    cx, cy = c if c else (a.get("cx", 0), a.get("cy", 0))
    return sw_automation.draw_polygon(cx=cx, cy=cy, radius=a.get("radius", 25), sides=a.get("sides", 6),
                                      unit=a.get("unit"), rotation=a.get("rotation", 0.0),
                                      flats=a.get("flats", False))


def _draw_closed_profile(a: dict, local) -> Dict[str, Any]:
    wpts = a.get("world_points")
    if wpts:
        if a.get("points"):
            raise ValueError("draw_closed_profile: give 'points' (sketch x/y) or 'world_points', not both.")
        pts = [list(sw_automation.world_point_to_sketch(p, a.get("unit"))) for p in wpts]
    else:
        pts = a.get("points", [])
    return sw_automation.draw_closed_profile(points=pts, unit=a.get("unit"))


def _select_entity(arguments: dict) -> Dict[str, Any]:
    return sw_automation.select_entity(
        target_type=arguments.get("target_type", "face"),
        name=arguments.get("name"),
        point=arguments.get("point"),
        unit=arguments.get("unit"),
        direction=arguments.get("direction"),
        mark=arguments.get("mark", 0),
        append=arguments.get("append", False),
    )


def _select_by_centroid(arguments: dict) -> Dict[str, Any]:
    """Face by centroid or edge by midpoint (coordinates in mm)."""
    common = dict(
        body_index=arguments.get("body_index", 1),
        x=arguments.get("x"),
        y=arguments.get("y"),
        z=arguments.get("z"),
        tolerance=arguments.get("tolerance", 0.5),
        select=not arguments.get("locate_only", False),
    )
    if arguments.get("target_type") == "edge":
        return sw_automation.find_edge(**common)
    return sw_automation.find_face(
        min_area=arguments.get("min_area", 10.0),
        surface_type=arguments.get("surface_type", "plane"),
        **common,
    )


def _inspect_topology(arguments: dict) -> Dict[str, Any]:
    if arguments.get("edges"):
        # Python edge analysis: the MecAgent GetEdgeAnalysis ignored the face path, printed
        # metres as mm and reported box edges as 180°.
        return _edge_analysis_handler(str(arguments.get("path", "") or ""))
    runner = get_mecagent_runner()
    res = runner.invoke_sdk_method("navigate", {
        "path": arguments.get("path", ""),
        "showEquations": bool(arguments.get("show_equations", False)),
    })
    # The library prints metres/m² but labels them mm/mm²; convert so the labels are true.
    if isinstance(res.get("details"), str):
        res["details"] = _fix_topology_units(res["details"])
        if res.get("success") and res["details"].strip().startswith("No active model"):
            res["success"] = False
            res["errorMessage"] = res["details"]
    return _runner_result(res, None, "Topology navigation failed")


def _transaction(arguments: dict) -> Dict[str, Any]:
    action = arguments.get("action", "begin")
    if action in ("checkpoint", "rollback"):
        if not arguments.get("name"):
            return sw_automation._result(False, f"'name' is required for action '{action}'", SwErrors.swInvalidInput)
        return _checkpoint("create" if action == "checkpoint" else "rollback")(arguments)
    return sw_automation.transaction(action=action, name=arguments.get("name") or "MCP batch")


def _search_knowledge(arguments: dict) -> Dict[str, Any]:
    recs = get_knowledge_manager().search(
        query=arguments.get("query"),
        tags=arguments.get("tags"),
        interface=arguments.get("interface"),
        min_confidence=arguments.get("min_confidence", 0.0),
    )
    return _ok(f"Found {len(recs)} matching knowledge records",
               {"count": len(recs), "records": [r.to_dict() for r in recs]})


def _connected_sw_version() -> str:
    try:
        if sw_automation.is_connected and sw_automation._sw_app is not None:
            return str(get_attr_or_call(sw_automation._sw_app, "RevisionNumber"))
    except Exception:
        pass
    return "unknown"


def _record_knowledge(arguments: dict) -> Dict[str, Any]:
    try:
        rec = KnowledgeRecord(
            id=arguments.get("id", ""),
            title=arguments.get("title", ""),
            date=datetime.date.today().isoformat(),
            product_version=_connected_sw_version(),
            api_interface=arguments.get("api_interface", ""),
            api_member=arguments.get("api_member", ""),
            problem=arguments.get("problem", ""),
            attempted_approaches=arguments.get("attempted_approaches", []),
            successful_approach=arguments.get("successful_approach", ""),
            source="live_operation",
            confidence=float(arguments.get("confidence", 0.8)),
            verification_method="manual_or_live_recording",
            promotion_state=PromotionState(arguments.get("promotion_state", "discovered")),
            tags=arguments.get("tags", []),
        )
        path = get_knowledge_manager().save_record(rec)
        return _ok(f"Recorded knowledge '{rec.id}' in tier '{rec.promotion_state.value}'",
                   {"record": rec.to_dict(), "path": path})
    except Exception as e:
        return sw_automation._result(False, f"Failed to record knowledge: {e}", SwErrors.swInvalidInput)


def _checkpoint(action: str) -> Callable[[dict], Dict[str, Any]]:
    def handler(arguments: dict) -> Dict[str, Any]:
        doc, err = sw_automation.get_active_doc()
        if err:
            return err
        manager = get_checkpoint_manager()
        if action == "create":
            res = manager.create_checkpoint(
                doc=doc,
                name=arguments.get("name", "checkpoint"),
                description=arguments.get("description", ""),
            )
            data = res.get("checkpoint")
        else:
            res = manager.rollback_checkpoint(doc=doc, name=arguments.get("name", ""))
            data = res
        ok = bool(res.get("success"))
        return {
            "success": ok,
            "message": res.get("message", res.get("error", f"Failed to {action} checkpoint")),
            "error_code": 0 if ok else res.get("error_code", int(SwErrors.swUnknownError)),
            "error_name": "swSuccess" if ok else res.get("error_name", "swUnknownError"),
            "data": data,
        }
    return handler


def _execute_code(arguments: dict) -> Dict[str, Any]:
    code = arguments.get("code", "")
    if not code:
        return sw_automation._result(False, "Code is required", SwErrors.swInvalidInput)
    confirmed = bool(arguments.get("user_confirmed_risks", False))
    read_only = bool(arguments.get("read_only", False))
    language = arguments.get("language")

    if language == "python":
        return _execute_python_fixed(
            code,
            user_confirmed_risks=confirmed,
            reset_session=bool(arguments.get("reset_session", False)),
            read_only=read_only,
            auto_transaction=bool(arguments.get("auto_transaction", True)),
        )
    if language not in ("csharp", "csharp_macro"):
        return sw_automation._result(
            False, "'language' must be one of: python, csharp, csharp_macro", SwErrors.swInvalidInput)

    gate = _code_execution_gate("execute_code", confirmed)
    if gate:
        return gate
    runner = get_mecagent_runner()
    timeout_ms = int(arguments.get("timeout_ms", 120000))
    if language == "csharp":
        if arguments.get("reset_session"):
            runner.reset_session()
        # The active document type lets CS1061 cast repair pick IPartDoc / IAssemblyDoc / IDrawingDoc.
        hint = None
        try:
            _d, _ = sw_automation.get_active_doc()
            hint = sw_automation.get_document_type(_d) if _d is not None else None
        except Exception:
            hint = None
        res = runner.execute_csharp(code=code, read_only_only=read_only, timeout_ms=timeout_ms, doc_type_hint=hint)
        return _runner_result(res, "C# cell executed", "C# cell failed")
    res = runner.execute_macro(
        code=code, parameters=arguments.get("parameters"), read_only_only=read_only, timeout_ms=timeout_ms)
    return _runner_result(res, "C# macro executed", "C# macro failed")


def _capture_views(arguments: dict) -> Dict[str, Any]:
    res = get_mecagent_runner().capture_views(save_dir=arguments.get("save_dir"))
    return _runner_result(res, "Captured views", "View capture failed")


def _context_fast(arguments: dict) -> Dict[str, Any]:
    res = get_mecagent_runner().invoke_sdk_method("getContextFast", {
        "depth": int(arguments.get("depth", 2)),
        "targetPath": arguments.get("target_path"),
        "documentName": arguments.get("document_name"),
    })
    summary = f"Context retrieved (features={res.get('featureCount', 0)}, selections={res.get('selectionCount', 0)})"
    return _runner_result(res, summary, "Failed to get fast context")


_NUM = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"


def _fix_topology_units(text: str) -> str:
    """Convert metre values that MecAgent's Navigate output labels as mm/mm² into real mm."""
    import re as _re

    def _scaled(segment: str, factor: float) -> str:
        return _re.sub(_NUM, lambda m: f"{float(m.group(0)) * factor:.3f}", segment)

    out_lines = []
    for line in text.splitlines():
        key = line.strip().split(":", 1)[0].strip()
        if ":" not in line:
            out_lines.append(line)
            continue
        head, tail = line.split(":", 1)
        if key == "Area":
            line = head + ":" + _scaled(tail, 1e6)
        elif key in ("Length", "Dimensions", "Center", "Bounding Box", "UV Bounds", "Start", "End", "Mid"):
            tail = _scaled(tail, 1000.0)
            if "mm" not in tail:
                tail = tail.rstrip() + " mm"
            line = head + ":" + tail
        out_lines.append(line)
    return "\n".join(out_lines)


def _edge_analysis_handler(face_path: str) -> Dict[str, Any]:
    """
    Edge table for one face ('Feature/Face[i]', 0-based like sw_navigate_topology) or for body 1:
    length (mm), curve type, start/end/mid (mm), and dihedral angle between the two adjacent
    faces for planar/planar edges (90 = square corner, <180 convex, >180 concave).
    Replaces the MecAgent GetEdgeAnalysis call, which ignored face_path, reported metres as mm
    and printed 180° for the square edges of a box.
    """
    import math as _math
    import re as _re

    doc, err = sw_automation.get_active_doc()
    if err:
        return err

    def _vec(p):
        return [float(p[0]), float(p[1]), float(p[2])]

    def _plane_normal(face):
        """Outward unit normal of a planar face, or None for non-planar faces."""
        try:
            surf = get_attr_or_call(face, "GetSurface")
            if surf is None or not bool(get_attr_or_call(surf, "IsPlane")):
                return None
            # IFace2.Normal already accounts for the face's sense relative to its surface
            # (outward for a solid). Raw PlaneParams + FaceInSurfaceSense handling inverted
            # the normals and reported box edges as 270° concave.
            n = get_attr_or_call(face, "Normal")
            if n is None or len(n) < 3:
                params = get_attr_or_call(surf, "PlaneParams")
                n = [float(params[0]), float(params[1]), float(params[2])]
                sense = get_attr_or_call(face, "FaceInSurfaceSense")
                if callable(sense):
                    sense = sense()
                if sense is not None and not bool(sense):
                    n = [-c for c in n]
            n = [float(n[0]), float(n[1]), float(n[2])]
            mag = _math.sqrt(sum(c * c for c in n)) or 1.0
            return [c / mag for c in n]
        except Exception:
            return None

    def _centroid(face):
        box = get_attr_or_call(face, "GetBox")
        return [(box[0] + box[3]) / 2, (box[1] + box[4]) / 2, (box[2] + box[5]) / 2] if box else None

    scope = "body 1"
    if face_path:
        m = _re.match(r"^\s*([^/]+)/Face\[(\d+)\]\s*$", face_path)
        if not m:
            return {"success": False, "message": f"face_path must look like 'Feature/Face[0]', got '{face_path}'.",
                    "error_code": int(SwErrors.swInvalidInput), "error_name": "swInvalidInput"}
        feat = doc.FeatureByName(m.group(1))
        faces = list(get_attr_or_call(feat, "GetFaces") or []) if feat is not None else []
        idx = int(m.group(2))
        if not faces or idx >= len(faces):
            return {"success": False, "message": f"Face '{face_path}' not found (feature has {len(faces)} faces).",
                    "error_code": int(SwErrors.swSelectionError), "error_name": "swSelectionError"}
        edges = list(get_attr_or_call(faces[idx], "GetEdges") or [])
        scope = face_path
    else:
        bodies = get_attr_or_call(doc, "GetBodies2", 0, False) or []
        if not bodies:
            return {"success": False, "message": "No solid body in the active document.",
                    "error_code": int(SwErrors.swGeneralError), "error_name": "swGeneralError"}
        edges = list(get_attr_or_call(bodies[0], "GetEdges") or [])

    rows = []
    for i, e in enumerate(edges):
        row: Dict[str, Any] = {"index": i}
        try:
            crv = get_attr_or_call(e, "GetCurve")
            if crv is not None and bool(get_attr_or_call(crv, "IsLine")):
                row["type"] = "line"
            elif crv is not None and bool(get_attr_or_call(crv, "IsCircle")):
                row["type"] = "circle/arc"
                try:
                    row["radius_mm"] = round(float(get_attr_or_call(crv, "CircleParams")[6]) * 1000, 3)
                except Exception:
                    pass
            else:
                row["type"] = "other"
            v1, v2 = get_attr_or_call(e, "GetStartVertex"), get_attr_or_call(e, "GetEndVertex")
            p1 = _vec(get_attr_or_call(v1, "GetPoint")) if v1 else None
            p2 = _vec(get_attr_or_call(v2, "GetPoint")) if v2 else None
            cp = get_attr_or_call(e, "GetCurveParams3")
            t0, t1 = float(get_attr_or_call(cp, "UMinValue")), float(get_attr_or_call(cp, "UMaxValue"))
            length = float(crv.GetLength3(t0, t1)) if crv is not None else 0.0
            row["length_mm"] = round(length * 1000, 3)
            if p1:
                row["start_mm"] = [round(c * 1000, 3) for c in p1]
            if p2:
                row["end_mm"] = [round(c * 1000, 3) for c in p2]
            if p1 and p2 and row["type"] == "line":
                row["mid_mm"] = [round((a + b) * 500, 3) for a, b in zip(p1, p2)]
            adj = [f for f in (get_attr_or_call(e, "GetTwoAdjacentFaces2") or []) if f is not None]
            if len(adj) == 2:
                n1, n2 = _plane_normal(adj[0]), _plane_normal(adj[1])
                if n1 and n2:
                    cosang = max(-1.0, min(1.0, sum(a * b for a, b in zip(n1, n2))))
                    between_normals = _math.degrees(_math.acos(cosang))
                    c1, c2 = _centroid(adj[0]), _centroid(adj[1])
                    # Convex if face 2's centroid lies behind face 1's outward normal.
                    convex = True
                    if c1 and c2:
                        convex = sum((b - a) * n for a, b, n in zip(c1, c2, n1)) <= 1e-9
                    interior = 180.0 - between_normals if convex else 180.0 + between_normals
                    row["dihedral_deg"] = round(interior, 2)
                    row["convex"] = convex
        except Exception as ex:
            row["error"] = str(ex)
        rows.append(row)

    lines = [f"Edge analysis for {scope}: {len(rows)} edge(s) (lengths/coords in mm)"]
    for r in rows:
        ang = f" angle={r['dihedral_deg']}° {'convex' if r.get('convex') else 'concave'}" if "dihedral_deg" in r else ""
        rad = f" r={r['radius_mm']}" if "radius_mm" in r else ""
        lines.append(f"[{r['index']}] {r.get('type', '?')} L={r.get('length_mm', '?')}{rad}{ang} mid={r.get('mid_mm', r.get('start_mm'))}")
    return {
        "success": True,
        "message": "\n".join(lines),
        "error_code": 0,
        "error_name": "swSuccess",
        "data": {"scope": scope, "edges": rows, "count": len(rows)},
    }


def _modify_feature_parameter_by_path_handler(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply depth / fillet radius / reverse / draft changes to one feature.

    The MecAgent controller expects (path, parameterID, value) where parameterID is a full
    dimension name such as 'D1@Boss-Extrude1@part.Part'; the old wrapper sent unrelated keys
    so every call failed with "Unrecognized value format: ''".
    """
    feature_path = str(arguments.get("feature_path", "") or "").strip()
    feat_name = feature_path.split("/")[0]
    if not feat_name:
        return {"success": False, "message": "feature_path is required.", "error_code": int(SwErrors.swInvalidInput), "error_name": "swInvalidInput"}

    numeric_requests = []
    if arguments.get("depth_mm") is not None:
        numeric_requests.append(("depth_mm", float(arguments["depth_mm"])))
    if arguments.get("fillet_radius_mm") is not None:
        numeric_requests.append(("fillet_radius_mm", float(arguments["fillet_radius_mm"])))
    flag_requests = {k: arguments.get(k) for k in ("reverse_direction", "draft_enabled", "draft_angle_deg") if arguments.get(k) is not None}

    if not numeric_requests and not flag_requests:
        return {
            "success": False,
            "message": "Nothing to change: pass depth_mm, fillet_radius_mm, reverse_direction, draft_enabled or draft_angle_deg.",
            "error_code": int(SwErrors.swInvalidInput), "error_name": "swInvalidInput",
        }

    applied: List[Dict[str, Any]] = []
    errors: List[str] = []

    if numeric_requests:
        dims_res = sw_automation.get_dimensions(feature_name=feat_name)
        dims = [d for d in (dims_res.get("data") or {}).get("dimensions", []) if d.get("owner_feature") == feat_name]
        linear = [d for d in dims if d.get("units") not in ("deg", "count")]
        if not linear:
            errors.append(f"No linear dimension found on feature '{feat_name}'.")
        else:
            target = linear[0]  # D1 = depth (extrude/cut) or radius (constant fillet)
            runner = get_mecagent_runner()
            for key, val in numeric_requests:
                res = runner.invoke_sdk_method(
                    "modifyFeatureParameterByPath",
                    {"path": feat_name, "parameterId": target["full_name"], "value": f"{val}"},
                )
                if res.get("success"):
                    applied.append({"parameter": key, "dimension": target["full_name"], "value_mm": val})
                else:
                    # Fall back to the native dimension setter (verifies + rolls back on failure).
                    sd = sw_automation.set_dimension(name=target["full_name"], value=val, units="mm")
                    if sd.get("success"):
                        applied.append({"parameter": key, "dimension": target["full_name"], "value_mm": val, "via": "set_dimension"})
                    else:
                        errors.append(f"{key}: {res.get('errorMessage') or res.get('error')} / {sd.get('message')}")

    if flag_requests:
        doc, err = sw_automation.get_active_doc()
        if err:
            errors.append(err.get("message", "No active document"))
        else:
            try:
                feat = doc.FeatureByName(feat_name)
                if feat is None:
                    raise RuntimeError(f"Feature '{feat_name}' not found")
                fdef = get_attr_or_call(feat, "GetDefinition")
                if fdef is None or not hasattr(fdef, "ReverseDirection"):
                    raise RuntimeError(f"Feature '{feat_name}' is not an extrude/cut; reverse/draft not applicable")
                fdef.AccessSelections(doc, None)
                if "reverse_direction" in flag_requests:
                    fdef.ReverseDirection = bool(flag_requests["reverse_direction"])
                if "draft_enabled" in flag_requests or "draft_angle_deg" in flag_requests:
                    enable = bool(flag_requests.get("draft_enabled", True))
                    fdef.SetDraftWhileExtruding(True, enable)
                    if "draft_angle_deg" in flag_requests:
                        import math as _math
                        fdef.SetDraftAngle(True, _math.radians(float(flag_requests["draft_angle_deg"])))
                ok_mod = bool(feat.ModifyDefinition(fdef, doc, None))
                if not ok_mod:
                    fdef.ReleaseSelectionAccess()
                    raise RuntimeError("ModifyDefinition returned False")
                applied.append({"parameter": "flags", **flag_requests})
            except Exception as fx:
                errors.append(f"reverse/draft: {fx}")

    if not errors:
        _doc_after, _ = sw_automation.get_active_doc()
        if _doc_after is not None:
            get_attr_or_call(_doc_after, "EditRebuild3")
    ok = not errors
    return {
        "success": ok,
        "message": (f"Modified '{feat_name}': {applied}" if ok else f"Modification of '{feat_name}' failed: {'; '.join(errors)}"),
        "error_code": 0 if ok else int(SwErrors.swGeneralError),
        "error_name": "swSuccess" if ok else "swGeneralError",
        "data": {"applied": applied, "errors": errors},
    }


_TOOL_HANDLERS: Dict[str, Callable[[dict], Dict[str, Any]]] = {
    # Connection
    'get_capabilities': _get_capabilities,
    'connect_solidworks': _auto('connect', 'pid'),
    'open_document': _auto('open_document', ('filepath', '')),
    'save_document': _auto('save_document', 'filepath'),
    'close_document': _auto('close_document', ('save', False)),
    'get_document_info': _auto('get_document_info'),
    'list_open_documents': _auto('list_open_documents'),
    'manage_configuration': _auto('manage_configuration', ('action', 'list'), 'name', 'new_name', 'description', 'comment'),
    'manage_custom_properties': _auto('manage_custom_properties', ('action', 'list'), 'name', 'value', ('property_type', 'text'), ('configuration', '')),
    'draw_line': _world_draw(_draw_line),
    'draw_circle': _world_draw(_draw_circle),
    'draw_rectangle': _world_draw(_draw_rectangle),
    'draw_arc': _world_draw(_draw_arc),
    'draw_polygon': _world_draw(_draw_polygon),
    'draw_text': _auto('draw_text', ('text', ''), ('x', 0.0), ('y', 0.0), ('height', 12.0), ('font_name', 'Segoe UI'), ('bold', True), ('italic', False), ('angle', 0.0), ('center', False), 'unit'),
    'extrude_sketch': _auto('extrude_sketch', ('depth', 10), ('both_directions', False), ('reverse_dir', False), 'unit', ('draft_angle', 0.0), ('draft_outward', False), ('merge', True)),
    'combine_bodies': _auto('combine_bodies', ('operation', 'add'), 'main_body', 'tool_bodies'),
    'cut_extrude': _auto('cut_extrude', ('depth', 10), ('through_all', False), ('both_directions', False), 'unit', 'reverse_dir'),
    'fillet_edges': _auto('fillet_edges', ('radius', 2), 'unit'),
    'chamfer_edges': _auto('chamfer_edges', ('distance', 2), ('angle', 45), 'unit'),
    'get_dimensions': _auto('get_dimensions', 'feature_name', 'dimension_name'),
    'set_dimension': _auto('set_dimension', 'name', 'value', 'units', ('config_scope', 'this_configuration')),
    'manage_feature': _auto('manage_feature', ('action', 'suppress'), ('feature_name', ''), 'new_name', 'target_feature', ('move_location', 'after'), ('delete_absorbed', True), ('delete_children', False), ('config_scope', 1), 'color_rgb'),
    'revolve_sketch': _auto('revolve_sketch', ('angle', 360.0), ('reverse_dir', False), ('is_thin', False), ('thin_thickness', 1.0), ('is_cut', False), 'axis_name', 'unit'),
    'create_shell': _auto('create_shell', ('thickness', 1.0), ('outward', False), 'unit', 'face_indices', ('body_index', 1)),
    'create_pattern': _auto('create_pattern', ('pattern_type', 'linear'), 'feature_name', ('num1', 2), ('spacing1', 10.0), ('flip_dir1', False), ('num2', 1), ('spacing2', 0.0), ('flip_dir2', False), ('angle', 360.0), ('equal_spacing', True), 'unit', 'direction1', 'direction2', 'mirror_plane', ('merge', True)),
    'sweep_tube': _auto('sweep_tube', ('points', []), ('diameter', 8.0), ('bend_radius', 0.0), ('merge', True), 'unit'),
    'create_hole_wizard': _auto('create_hole_wizard', ('hole_type', 'counterbore'), ('standard', 'ISO'), ('fastener_type', 'Hex Bolt'), ('size', 'M6'), ('end_condition', 'through_all'), 'depth', 'diameter', 'unit'),
    'inspect_model': _auto('inspect_model'),
    'verify_model': _auto('verify_model', 'expected_dx', 'expected_dy', 'expected_dz', ('expected_bodies', 1), ('tolerance_mm', 0.2)),
    'add_component': _auto('add_component', ('component_path', ''), ('x', 0.0), ('y', 0.0), ('z', 0.0), 'unit', 'rotation_deg', 'fixed'),
    'add_mate': _auto('add_mate', ('mate_type', 'coincident'), ('entity1', ''), ('entity2', ''), 'comp1', 'comp2', ('align', 0), ('flip', False), ('distance', 0.0), ('angle', 0.0), 'min_limit', 'max_limit', 'unit', ('use_selection', False)),
    'check_interference': _auto('check_interference', ('treat_coincidence_as_interference', False)),
    'list_components': _auto('list_components'),
    'create_drawing_from_model': _auto('create_drawing_from_model', 'model_path', ('view_name', '*Isometric'), ('x', 0.1), ('y', 0.1), ('paper_size', 'A4')),
    'export_drawing': _auto('export_drawing', ('filepath', ''), ('format', 'pdf')),
    'import_model_items': _auto('import_model_items', ('option', 1), ('types', 1), ('all_views', True), ('duplicate_dims', False), ('hidden_feature_dims', False), ('use_placement_in_sketch', True)),
    'add_drawing_dimension': _auto('add_drawing_dimension', ('x', 0.0), ('y', 0.0), ('z', 0.0), 'unit'),
    'create_section_view': _auto('create_section_view', ('x', 0.0), ('y', 0.0), ('section_label', 'A'), ('options', 32), 'line_x1', 'line_y1', 'line_x2', 'line_y2', 'parent_view', 'unit'),
    'insert_bom_table': _auto('insert_bom_table', 'template_path', ('x', 0.0), ('y', 0.0), ('bom_type', 'top_level'), ('anchor_type', 1), ('configuration', ''), 'unit'),
    'create_simulation_study': _auto('create_simulation_study', ('study_name', 'StaticStudy'), ('study_type', 'static')),
    'set_study_material': _auto('set_study_material', ('material_name', 'AISI 1020'), 'database_path', ('config_name', 'Default')),
    'add_study_fixture': _auto('add_study_fixture', ('fixture_type', 'fixed'), 'direction', 'face_point', 'face_name', 'unit', 'study_name'),
    'add_study_load': _auto('add_study_load', ('load_type', 'pressure'), ('value', 100000.0), ('unit', 'N/m^2'), 'direction', 'face_point', 'face_name', 'coord_unit', 'study_name'),
    'mesh_and_run_simulation': _auto('mesh_and_run_simulation', ('element_size_mm', 2.0), ('tolerance_mm', 0.1), 'study_name'),
    'get_simulation_results': _auto('get_simulation_results', 'study_name'),
    'inspect_dialogs': _auto('inspect_dialogs'),
    'dismiss_dialog': _auto('dismiss_dialog', 'hwnd', ('action', 'OK'), ('force_close', False)),
    'load_addin': _auto('load_addin', ('name_or_path', 'name', '')),
    'inspect_rebuild_errors': _auto('inspect_rebuild_errors'),
    'draw_closed_profile': _world_draw(_draw_closed_profile),
    'hole_linear': _auto('hole_linear', ('face_axis', 'z'), ('face_value', 0.0), ('ref1_axis', 'x'), ('ref1_value', 0.0), ('dist1', 5.0), ('ref2_axis', 'y'), ('ref2_value', 0.0), ('dist2', 5.0), ('diameter', 5.5), ('depth', 0.0), ('tapped', False), ('cbore_diameter', 0.0), ('cbore_depth', 0.0)),
    'set_sheet_metal_thickness': _auto('set_sheet_metal_thickness', ('thickness_mm', 1.5), 'bend_radius_mm', ('k_factor', 0.5)),
    'sheet_metal_face': _auto('sheet_metal_face', 'thickness_mm', 'bend_radius_mm', ('reverse_dir', False), 'depth', ('both_directions', False), ('reverse_extrude', False)),
    'flange': _auto('flange', 'edge_indices', ('distance', 25.0), ('angle', 90.0), ('body_index', 1), ('position', 'material_inside'), ('plain_wall', False), 'end_offsets'),
    'sheet_metal_cut': _auto('sheet_metal_cut', ('through_all', True), 'depth', ('normal_cut', True)),
    'delete_assembly_constraint': _auto('delete_assembly_constraint', ('constraint_name', '')),
    'manage_subassembly_kinematics': _auto('manage_subassembly_kinematics', ('action', 'set_flexible'), ('occurrence_name', ''), 'exclude_keywords'),
    'inspect_assembly_kinematics': _auto('inspect_assembly_kinematics'),
    'drive_kinematic_motion': _auto('drive_kinematic_motion', ('constraint_name', ''), ('start_value', 0.0), ('end_value', 90.0), ('steps', 10), ('unit', 'mm'), ('check_interference', False)),
    'manage_positional_representations': _auto('manage_positional_representations', ('action', 'list'), 'name', 'constraint_name', 'value', 'suppress_constraint'),
    'apply_material_appearance': _auto('apply_material_appearance', ('appearance_name', 'Smooth - White'), 'color_rgb', 'reflectivity', 'transparency', ('target_type', 'part'), 'target_name'),
    'set_material': _auto('set_material', 'material_name', ('database', 'SOLIDWORKS Materials'), ('configuration', '')),
    # Merged tools: one name, routed by a mode argument to the same underlying operations
    'create_document': _route('type', {
        'part': _auto('create_new_part'),
        'assembly': _auto('create_new_assembly'),
        'drawing': _auto('create_new_drawing', ('paper_size', 'A4')),
    }, default='part'),
    'create_sketch': _create_sketch,
    'get_feature_tree': _route('detail', {
        'list': lambda a: _list_features_fixed(),
        'parametric': _sdk("getFeatureTreeJson", "Failed to build feature tree JSON",
                           "Built parametric feature tree JSON IR"),
        'context': _context_fast,
    }, default='list'),
    'select': _route('by', {
        'name': _select_entity,
        'point': _select_entity,
        'geometry': _auto('select_by_geometry', ('target_type', 'face'), 'surface_type', 'normal', ('normal_tolerance', 0.05), 'radius', ('radius_tolerance', 0.05), 'point_on_surface', 'curve_type', 'length', ('length_tolerance', 0.1), 'midpoint', 'point', 'bbox_min', 'bbox_max', ('mark', 0), ('append', False), 'unit', 'component', ('max_results', 200), ('time_budget_s', 40.0), 'axis'),
        'centroid': _select_by_centroid,
        'path': _sdk("selectByPath", "Selection by path failed", build=lambda a: {
            "path": a.get("path", ""), "multiSelect": bool(a.get("append", False)), "mark": int(a.get("mark", 0))}),
    }),
    'list_topology': _route('kind', {
        'faces': _auto('list_faces', ('body_index', 1), ('min_area', 'min_size', 0.0), ('max_count', 60)),
        'edges': _auto('list_edges', ('body_index', 1), ('min_length', 'min_size', 0.0), ('max_count', 60)),
        'bodies': _auto('list_bodies'),
    }),
    'inspect_topology': _inspect_topology,
    'capture_viewport': _route('mode', {
        'view': _auto('capture_viewport', 'filepath', ('view_name', '*Isometric')),
        'overview': _capture_views,
        'showcase': _auto('render_showcase_viewport', 'eye_mm', 'target_mm', 'up_vec', ('display_style', 'shaded_with_edges'), 'filepath'),
    }, default='view'),
    'transaction': _transaction,
    'manage_parameters': _route('action', {
        'list': _auto('get_parameters'),
        'add': _needs('name', 'expression')(_auto('add_parameter', ('name', ''), ('expression', ''), ('units', 'mm'))),
        'set': _needs('name', 'expression')(_auto('set_parameter', ('name', ''), ('expression', ''), ('units', 'mm'))),
    }, default='list'),
    'execute_code': _execute_code,
    # Tools with custom glue
    'close_sketch': lambda a: _close_sketch_handler(),
    'get_sketch_status': lambda a: _get_sketch_status_handler(),
    'set_units': _auto('set_units', ('unit', 'mm')),
    'show_interactive_studio': lambda a: _show_interactive_studio_handler(a),
    'studio_action': lambda a: _studio_action_handler(a),
    'search_knowledge': _search_knowledge,
    'record_knowledge': _record_knowledge,
    'supervisor_clean_processes': lambda a: sw_automation.supervisor_clean_processes(
        confirm=bool(a.get("confirm", False)), target_pids=a.get("target_pids")),
    'reload_api': lambda a: _reload_api_handler(),
    # .NET runner
    'find_related_files': _sdk("findRelatedCadFiles", "Related file discovery failed", "Discovered related CAD files",
                               build=lambda a: {"maxDrawingsProbed": int(a.get("max_drawings_probed", 200))}),
    # The controller expects (path, parameterId, value); the old payload keys never matched, so every
    # call failed with "Unrecognized value format: ''". The handler resolves the dimension name.
    'modify_feature_by_path': lambda a: _modify_feature_parameter_by_path_handler(a),
}


def _session_unit() -> str:
    """The unit chosen with set_units, as a plain string."""
    unit = getattr(sw_automation.units, "default_unit", "mm")
    unit = getattr(unit, "value", unit)
    return unit if isinstance(unit, str) and unit else "mm"


def _dispatch_tool(name: str, arguments: dict) -> Dict[str, Any]:
    """Execute tool logic (runs on the STA worker thread)."""
    from .core.mecagent_runner import RunnerNotInstalledError
    try:
        return _dispatch(name, arguments)
    except RunnerNotInstalledError as e:
        return sw_automation._result(False, str(e), SwErrors.swGeneralError)


def _dispatch(name: str, arguments: dict) -> Dict[str, Any]:
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return sw_automation._result(False, f"Unknown tool: {name}", SwErrors.swUnknownError)
    # Every tool measures lengths in the session unit unless the caller says otherwise.
    unit_args = length_unit_arguments(name)
    if any(arguments.get(k) is None for k in unit_args):
        arguments = dict(arguments)
        for key in unit_args:
            if arguments.get(key) is None:
                arguments[key] = _session_unit()
    return handler(arguments)



def _dispatch_dialog_tool(name: str, arguments: dict) -> Dict[str, Any]:
    """Execute Win32-only dialog inspection and dismissal independently of STA worker queue."""
    if name == "inspect_dialogs":
        return sw_automation.inspect_dialogs()
    elif name == "dismiss_dialog":
        return sw_automation.dismiss_dialog(
            hwnd=arguments.get("hwnd"),
            action=arguments.get("action", "OK"),
            force_close=arguments.get("force_close", False),
        )
    return sw_automation._result(False, f"Unknown dialog tool: {name}", SwErrors.swUnknownError)


_PART_MUTATING_TOOLS = {
    "extrude_sketch",
    "sweep_tube",
    "combine_bodies",
    "cut_extrude",
    "revolve_sketch",
    "fillet_edges",
    "chamfer_edges",
    "create_shell",
    "create_pattern",
    "create_hole_wizard",
    "set_dimension",
    "manage_feature",
    "hole_linear",
    "sheet_metal_face",
    "flange",
    "sheet_metal_cut",
    "modify_feature_by_path",
    "manage_parameters",
    "execute_code",
}

# Tools that hand control to the user or run a long solver: leave SolidWorks' normal idle processing on.
_NO_COMMAND_IN_PROGRESS_TOOLS = {
    "mesh_and_run_simulation",
    "show_interactive_studio",
    "studio_action",
    "supervisor_clean_processes",
}

_SUBTRACTIVE_TOOLS = {
    "cut_extrude",
    "create_hole_wizard",
    "sheet_metal_cut",
}


def _execute_tool_on_sta(name: str, arguments: dict) -> Tuple[Dict[str, Any], Optional[str], Optional[str], Optional[int]]:
    """Execute tool on STA worker thread and capture diagnostic metadata + volume delta without crossing thread boundaries."""
    tid = threading.get_ident()
    dlg = find_blocking_modal_dialog()
    if dlg:
        title = dlg.get("title", "Modal Dialog")
        msgs = dlg.get("messages", [])
        details = f"{title}: {' '.join(msgs)}" if msgs else title
        raise SolidWorksModalBlockedException(
            f"SolidWorks is blocked by modal dialog: {details}",
            dlg,
            dispatched=False,
        )

    # ISldWorks.CommandInProgress tells SolidWorks that an out-of-process client is running a batch of
    # API calls. Measured on SW 2025: 5.6 ms -> 0.28 ms per COM call (a face scan of a 5,300-face
    # assembly went from minutes to seconds, MCP-019). It must be switched off again afterwards.
    cip_app = None
    if name not in _NO_COMMAND_IN_PROGRESS_TOOLS:
        try:
            if sw_automation.is_connected and sw_automation._sw_app is not None:
                sw_automation._sw_app.CommandInProgress = True
                cip_app = sw_automation._sw_app
        except Exception:
            cip_app = None
    try:
        return _execute_tool_on_sta_inner(name, arguments, tid)
    finally:
        # The tool may have (re)connected: clear the flag on the app it was set on and the current one.
        for app in {id(a): a for a in (cip_app, getattr(sw_automation, "_sw_app", None)) if a is not None}.values():
            try:
                app.CommandInProgress = False
            except Exception:
                pass


def _execute_tool_on_sta_inner(name: str, arguments: dict, tid: int) -> Tuple[Dict[str, Any], Optional[str], Optional[str], Optional[int]]:
    vol_before = None
    if name in _PART_MUTATING_TOOLS:
        try:
            if sw_automation.is_connected and sw_automation._sw_app is not None:
                doc_pre = sw_automation._sw_app.ActiveDoc
                if doc_pre is not None:
                    vol_before = capture_vol_state(doc_pre)
        except Exception:
            pass

    result = _dispatch_tool(name, arguments)
    sw_ver = None
    act_doc = None
    try:
        if sw_automation.is_connected and sw_automation._sw_app is not None:
            rev = sw_automation._sw_app.RevisionNumber
            sw_ver = str(rev() if callable(rev) else rev)
            doc = sw_automation._sw_app.ActiveDoc
            if doc is not None:
                title = doc.GetTitle
                act_doc = str(title() if callable(title) else title)
                if name in _PART_MUTATING_TOOLS and isinstance(result, dict) and result.get("success"):
                    vol_after = capture_vol_state(doc)
                    suffix, delta_dict = format_vol_delta(vol_before, vol_after)
                    if suffix:
                        cur_msg = str(result.get("message", ""))
                        if " | V " not in cur_msg:
                            result["message"] = f"{cur_msg}{suffix}"
                        if not isinstance(result.get("data"), dict):
                            result["data"] = {"value": result.get("data")}
                        result["data"]["geometry_delta"] = delta_dict
                        if name in _SUBTRACTIVE_TOOLS and delta_dict and abs(delta_dict.get("volume_delta_mm3", 0.0)) < 0.01:
                            warn_txt = " [WARNING: ΔV=0 mm³, check cut direction/sketch placement]"
                            result["message"] = f"{result['message']}{warn_txt}"
                            result["data"]["zero_volume_change_warning"] = True
    except Exception:
        pass
    return result, sw_ver, act_doc, tid


_NEUTRAL_EXTS = (".step", ".stp", ".iges", ".igs", ".x_t", ".x_b", ".sat", ".stl")


def get_tool_timeout(tool_name: str, cfg: Any, arguments: Optional[dict] = None) -> float:
    """Get per-operation timeout based on configuration (and, for file tools, on the file)."""
    if tool_name in ("open_document", "add_component") and arguments:
        path = str(arguments.get("filepath") or arguments.get("component_path") or "")
        if path.lower().endswith(_NEUTRAL_EXTS):
            # Translating a multi-megabyte STEP assembly takes minutes.
            return float(getattr(cfg, "import_timeout", 900.0))
        if tool_name == "add_component":
            return float(getattr(cfg, "open_save_timeout", 120.0)) * 2
    if tool_name == "select" and arguments:
        # The geometry scan stops itself at time_budget_s; give it room to return its partial result.
        try:
            budget = float(arguments.get("time_budget_s") or 40.0)
        except (TypeError, ValueError):
            budget = 40.0
        return max(float(getattr(cfg, "default_timeout", 60.0)), budget + 30.0)
    if tool_name in (
        "open_document",
        "save_document",
        "close_document",
        "create_document",
        "create_drawing_from_model",
        "connect_solidworks",
        "execute_code",
        "capture_viewport",
        "find_related_files",
    ):
        return float(getattr(cfg, "open_save_timeout", 120.0))
    elif tool_name in ("export_drawing",):
        return float(getattr(cfg, "export_timeout", 180.0))
    elif tool_name in ("mesh_and_run_simulation",):
        return float(getattr(cfg, "simulation_timeout", 600.0))
    else:
        return float(getattr(cfg, "default_timeout", 60.0))


async def call_tool(
    ctx: ServerRequestContext,
    params: CallToolRequestParams,
) -> CallToolResult:
    """Handle MCP tool calls routed through the STA COM worker with watchdog and timeout."""
    try:
        name = params.name
        arguments = dict(params.arguments or {})
        validation_error = _validate_tool_arguments(name, arguments)
        if validation_error:
            err_env = error_envelope(
                message=validation_error,
                error_code=int(SwErrors.swInvalidInput),
                error_name="swInvalidInput",
                data={"validation_error": validation_error},
            )
            return err_env.to_call_tool_result()

        op_id = f"op_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        t_start = time.perf_counter()
        logger.info(f"Tool: {name}, Op: {op_id}, Args: {arguments}")

        cfg = get_config()

        if name in ("inspect_dialogs", "dismiss_dialog"):
            # Pure Win32 calls - execute in background thread (never blocked by STA queue)
            result = await asyncio.to_thread(_dispatch_dialog_tool, name, arguments)
            sw_ver = None
            act_doc = None
            tid = None
        elif name == "reset_sta_worker":
            # Administrative recovery tool - execute directly (never blocked by STA queue)
            from .core.com_worker import reset_sta_worker as do_reset_worker
            new_worker = do_reset_worker()
            sw_automation._sta = new_worker
            # Invalidate stale COM pointers tied to the abandoned worker thread
            sw_automation._sw_app = None
            sw_automation._connected = False
            result = {
                "success": True,
                "message": "STA COM worker forcefully reset. Orphaned worker abandoned, fresh worker initialized.",
                "error_code": 0,
                "data": {
                    "new_thread_id": new_worker.thread_id,
                    "abandoned_call_cleared": True,
                }
            }
            sw_ver = None
            act_doc = None
            tid = new_worker.thread_id
        else:
            timeout = get_tool_timeout(name, cfg, arguments)
            watchdog_timeout = float(getattr(cfg, "watchdog_timeout", 4.0))
            sta = get_sta_worker()
            result, sw_ver, act_doc, tid = await sta.run_async(
                _execute_tool_on_sta,
                name,
                arguments,
                timeout=timeout,
                watchdog_timeout=watchdog_timeout,
                tool_name=name,
            )

        duration_ms = (time.perf_counter() - t_start) * 1000
        envelope = create_envelope(result, operation_id=op_id)
        envelope.diagnostics.duration_ms = duration_ms
        if sw_ver:
            envelope.diagnostics.sw_version = sw_ver
        if act_doc:
            envelope.diagnostics.active_document = act_doc
        if tid:
            envelope.diagnostics.thread_id = tid
        envelope.diagnostics.unit = sw_automation.units.default_unit

        logger.info(f"Result: success={envelope.success}, op={op_id}, duration={duration_ms:.2f}ms")
        if name in ("show_interactive_studio", "studio_action") and envelope.success:
            # MCP Apps hosts forward structuredContent to the studio view, which renders the A2UI messages.
            return CallToolResult(
                content=[TextContent(type="text", text=result.get("message", ""))],
                structured_content=result.get("data"),
                is_error=False,
                meta={"ui": {"resourceUri": STUDIO_RESOURCE_URI}},
            )
        return envelope.to_call_tool_result()

    except SolidWorksModalBlockedException as e:
        duration_ms = (time.perf_counter() - t_start) * 1000 if "t_start" in locals() else 0.0
        current_op = op_id if "op_id" in locals() else f"op_err_{uuid.uuid4().hex[:6]}"
        logger.warning(f"Tool '{name}' blocked by modal dialog: {e}")
        dlg = getattr(e, "dialog_info", {})
        dispatched = getattr(e, "dispatched", True)
        age_s = round(duration_ms / 1000.0, 2)
        if dispatched:
            msg = f"{e}. Warning: tool '{name}' was started on the server and may still execute once unblocked. Inspect model/document state before retrying."
        else:
            msg = f"{e}. Tool '{name}' was NOT dispatched to SolidWorks and will not execute."
        err_env = error_envelope(
            message=msg,
            error_code=int(SwErrors.swModalDialogBlocking),
            error_name="swModalDialogBlocking",
            data={
                "dialog": dlg,
                "blocked_by_dialog": True,
                "may_still_execute": dispatched,
                "dispatched": dispatched,
                "tool": name,
                "age_s": age_s,
            },
            details=f"blocked_by_dialog=True, may_still_execute={dispatched}",
            duration_ms=duration_ms,
            operation_id=current_op,
        )
        return err_env.to_call_tool_result()

    except TimeoutError as e:
        duration_ms = (time.perf_counter() - t_start) * 1000 if "t_start" in locals() else 0.0
        current_op = op_id if "op_id" in locals() else f"op_err_{uuid.uuid4().hex[:6]}"
        logger.error(f"Tool '{name}' timed out: {e}")
        age_s = round(duration_ms / 1000.0, 2)
        err_env = error_envelope(
            message=f"{e}. Warning: tool '{name}' was started on the server and may still execute in the background. Inspect model/document state before retrying.",
            error_code=int(SwErrors.swOperationTimeout),
            error_name="swOperationTimeout",
            details={
                "tool": name,
                "timeout_seconds": timeout if "timeout" in locals() else 60.0,
                "may_still_execute": True,
                "age_s": age_s,
            },
            duration_ms=duration_ms,
            operation_id=current_op,
        )
        return err_env.to_call_tool_result()

    except AbandonedCallRunningException as e:
        duration_ms = (time.perf_counter() - t_start) * 1000 if "t_start" in locals() else 0.0
        current_op = op_id if "op_id" in locals() else f"op_err_{uuid.uuid4().hex[:6]}"
        logger.error(f"Worker busy with abandoned call: {e}")
        abandoned_tool = getattr(e, "abandoned_tool", "")
        age_s = round(getattr(e, "age", 0.0), 2)
        err_env = error_envelope(
            message=f"{e}. The earlier call may still be running; inspect model/document state before retrying.",
            error_code=int(SwErrors.swWorkerBusy),
            error_name="swWorkerBusy",
            details={
                "abandoned_tool": abandoned_tool,
                "tool": abandoned_tool,
                "age_s": age_s,
                "may_still_execute": True,
            },
            duration_ms=duration_ms,
            operation_id=current_op,
        )
        return err_env.to_call_tool_result()


    except Exception as e:
        duration_ms = (time.perf_counter() - t_start) * 1000 if "t_start" in locals() else 0.0
        current_op = op_id if "op_id" in locals() else f"op_err_{uuid.uuid4().hex[:6]}"
        logger.error(f"Tool error: {e}\n{traceback.format_exc()}")
        err_env = error_envelope(
            message=f"Tool error: {e}",
            error_code=int(SwErrors.swGeneralError),
            error_name="swGeneralError",
            details=traceback.format_exc(),
            duration_ms=duration_ms,
            operation_id=current_op,
        )
        return err_env.to_call_tool_result()


async def list_resources(
    ctx: Optional[ServerRequestContext] = None,
    params: Optional[PaginatedRequestParams] = None,
) -> ListResourcesResult:
    """Advertise the interactive studio view (MCP Apps)."""
    return ListResourcesResult(
        resources=[
            Resource(
                uri=STUDIO_RESOURCE_URI,
                name="SolidWorks Interactive Studio",
                description="A2UI studio with live dimension sliders and a SolidWorks viewport preview.",
                mime_type=STUDIO_MIME_TYPE,
            )
        ]
    )


async def read_resource(
    ctx: Optional[ServerRequestContext],
    params: ReadResourceRequestParams,
) -> ReadResourceResult:
    """Serve the studio page. It is static: the surface arrives as A2UI messages in the tool result."""
    uri_str = str(getattr(params, "uri", ""))
    if uri_str != STUDIO_RESOURCE_URI:
        raise ValueError(f"Resource not found: {uri_str}")
    from .ui import bridge
    return ReadResourceResult(
        contents=[
            TextResourceContents(
                uri=STUDIO_RESOURCE_URI,
                mime_type=STUDIO_MIME_TYPE,
                # No network access: images are data URIs and actions go through the host.
                meta={"ui": {"csp": {"resourceDomains": ["data:"]}}},
                text=bridge.render_page(with_bridge=False),
            )
        ]
    )


server = Server(
    "solidworks-mcp-server",
    version=__version__,
    on_list_tools=list_tools,
    on_call_tool=call_tool,
    on_list_resources=list_resources,
    on_read_resource=read_resource,
)


# ============================================================================
# FIXED: Stateful Execute Python with stdout capture, FinalAnswer & Rollback
# ============================================================================

_session_exec_unlocked: bool = False
_python_exec_ns: Dict[str, Any] = {}


class _PythonFinalAnswerSignal(Exception):
    """Internal control-flow signal raised by FinalAnswer(val) in execute_python."""
    def __init__(self, value: Any):
        self.value = value


def _code_execution_gate(tool: str, user_confirmed_risks: bool) -> Optional[Dict[str, Any]]:
    """
    Consent gate for execute_code, whatever the language.

    Returns None when execution may proceed, otherwise the refusal result.
    Unlocked by config/env (SW_MCP_ALLOW_EXEC=1) or, for the rest of the session,
    by a call carrying user_confirmed_risks=true.
    """
    global _session_exec_unlocked
    env_on = any(
        os.environ.get(var, "").strip().lower() in ("1", "true", "yes", "on")
        for var in ("SW_MCP_ALLOW_EXEC", "SWMCP_ENABLE_EXECUTE_PYTHON")
    )
    if user_confirmed_risks:
        _session_exec_unlocked = True
    if get_config().enable_execute_python or env_on or _session_exec_unlocked:
        return None

    logger.warning(f"{tool} rejected: interactive consent required")
    return {
        "success": False,
        "message": (
            f"PERMISSION REQUIRED: `{tool}` is locked by default for security.\n\n"
            "AI Agent Directive:\n"
            "You MUST ask the human user for explicit permission in chat before executing arbitrary code.\n\n"
            "Explain the following to the user:\n"
            "- BENEFITS: Direct access to the full SolidWorks COM API for custom macros and operations "
            "not covered by the standard MCP tools.\n"
            "- RISKS: Runs arbitrary code with the current Windows user's privileges on the host system, "
            "bypassing MCP tool parameter guards and the filesystem allowlist.\n\n"
            f"Once the user gives explicit consent in chat, re-invoke `{tool}` with `user_confirmed_risks=true` "
            "or set environment variable `SW_MCP_ALLOW_EXEC=1`."
        ),
        "error_code": int(SwErrors.swInvalidCall),
        "error_name": "swInvalidCall",
        "security_gate": "consent_required",
        "timestamp": datetime.datetime.now().isoformat(),
    }


def _reload_api_handler() -> Dict[str, Any]:
    """Hot-reload solidworks_mcp submodules without dropping the active SolidWorks COM session."""
    import importlib
    reloaded = []
    errors = []
    skipped = []
    # com_worker owns the live STA thread singleton that every COM proxy belongs to.
    # Reloading it creates a second thread, after which the existing ISldWorks proxy
    # fails on every call ("SldWorks.Application.ActiveDoc"), so it is never reloaded.
    never_reload = {"solidworks_mcp.core.com_worker"}

    # Preserve in-memory checkpoint state across the reload.
    saved_checkpoints = None
    try:
        from .core import checkpoint as _ckpt_mod
        if getattr(_ckpt_mod, "_checkpoint_manager", None) is not None:
            saved_checkpoints = dict(_ckpt_mod._checkpoint_manager._checkpoints)
    except Exception:
        saved_checkpoints = None

    # Keep the running MecAgent runner process: reloading mecagent_runner reset its singleton,
    # which started a second runner and left the first one alive until the server exited.
    saved_runner = None
    try:
        _mr = sys.modules.get("solidworks_mcp.core.mecagent_runner")
        saved_runner = getattr(_mr, "_runner_bridge", None) if _mr else None
    except Exception:
        saved_runner = None

    def _order(name: str) -> int:
        # Leaves first, then core, then automation.base, then the mixins, then packages.
        if name.startswith("solidworks_mcp.utils."):
            return 0
        if name.startswith("solidworks_mcp.core."):
            return 1
        if name == "solidworks_mcp.automation.base":
            return 2
        if name.startswith("solidworks_mcp.automation."):
            return 3
        return 4

    prefixes = ("solidworks_mcp.core.", "solidworks_mcp.automation.", "solidworks_mcp.utils.")
    packages = ("solidworks_mcp.utils", "solidworks_mcp.core", "solidworks_mcp.automation")
    names = [n for n, m in list(sys.modules.items()) if m is not None and (n.startswith(prefixes) or n in packages)]
    for mod_name in sorted(names, key=_order):
        if mod_name in never_reload:
            skipped.append(mod_name)
            continue
        try:
            importlib.reload(sys.modules[mod_name])
            reloaded.append(mod_name)
        except Exception as ex:
            errors.append({"module": mod_name, "error": f"{type(ex).__name__}: {ex}"})

    # Point the live automation object at the freshly reloaded class so edited
    # tool implementations take effect without dropping the SolidWorks session.
    rebound = False
    try:
        new_cls = getattr(sys.modules["solidworks_mcp.automation"], "SolidWorksAutomation")
        sw_automation.__class__ = new_cls
        rebound = True
    except Exception as ex:
        errors.append({"module": "solidworks_mcp.automation", "error": f"class rebind failed: {ex}"})

    if saved_runner is not None:
        try:
            _mr = sys.modules["solidworks_mcp.core.mecagent_runner"]
            saved_runner.__class__ = _mr.MecAgentRunnerBridge  # pick up edited methods
            _mr._runner_bridge = saved_runner
        except Exception as ex:
            errors.append({"module": "solidworks_mcp.core.mecagent_runner", "error": f"runner rebind failed: {ex}"})

    if saved_checkpoints:
        try:
            from .core.checkpoint import get_checkpoint_manager as _gcm
            _gcm()._checkpoints.update(saved_checkpoints)
        except Exception as ex:
            errors.append({"module": "solidworks_mcp.core.checkpoint", "error": f"checkpoint restore failed: {ex}"})

    return {
        "success": len(errors) == 0,
        "message": f"Hot-reloaded {len(reloaded)} module(s) ({len(errors)} error(s)); COM session kept",
        "error_code": 0 if len(errors) == 0 else int(SwErrors.swGeneralError),
        "error_name": "swSuccess" if len(errors) == 0 else "swGeneralError",
        "data": {
            "reloaded_modules": reloaded,
            "skipped_modules": skipped,
            "automation_class_rebound": rebound,
            "errors": errors,
        },
    }


def _doc_fingerprint(doc: Any) -> Tuple[Any, Any]:
    """(volume state, feature count) - a cheap signature of the document's geometry."""
    count = None
    try:
        fm = getattr(doc, "FeatureManager", None)
        if fm is not None:
            count = fm.GetFeatureCount(True)
    except Exception:
        pass
    return capture_vol_state(doc), count


def _execute_python_fixed(
    code: str,
    user_confirmed_risks: bool = False,
    reset_session: bool = False,
    read_only: bool = False,
    auto_transaction: bool = True,
) -> Dict:
    """
    Execute custom Python code in a persistent stateful namespace with access to SolidWorks.
    Supports FinalAnswer(val), read_only fingerprint enforcement, and auto_transaction rollback.
    NOTE: Guarded by interactive consent gate and config flag 'enable_execute_python' (default: False).
    """
    global _python_exec_ns
    cfg = get_config()
    gate = _code_execution_gate("execute_code", user_confirmed_risks)
    if gate:
        return gate

    if reset_session:
        _python_exec_ns.clear()

    try:
        if not sw_automation.is_connected:
            if not getattr(cfg, "auto_connect", True):
                return {
                    "success": False,
                    "message": "Not connected to SolidWorks. Call connect_solidworks first.",
                    "error_code": int(SwErrors.swConnectionError),
                    "error_name": "swConnectionError",
                    "timestamp": datetime.datetime.now().isoformat(),
                }
            r = sw_automation.connect()
            if not r["success"]:
                return r
        
        import win32com.client
        import pythoncom
        import math
        import os as os_module

        doc = sw_automation.app.ActiveDoc if sw_automation.app else None
        vol_before = capture_vol_state(doc) if doc is not None else None
        feat_count_before = None
        if doc is not None:
            try:
                fm = getattr(doc, "FeatureManager", None)
                if fm is not None:
                    feat_count_before = fm.GetFeatureCount(True)
            except Exception:
                pass

        if auto_transaction and doc is not None:
            try:
                start_undo = getattr(doc, "StartUndoRecord", None)
                if callable(start_undo):
                    start_undo()
            except Exception:
                pass

        script_failed = False
        final_answer_box = {"called": False, "value": None}

        def _final_answer(val: Any = None):
            final_answer_box["called"] = True
            final_answer_box["value"] = val
            raise _PythonFinalAnswerSignal(val)

        # Update persistent namespace with fresh SolidWorks handles while keeping user variables
        _python_exec_ns.update({
            'sw': sw_automation.app,
            'doc': doc,
            'model': doc,
            'automation': sw_automation,
            'win32com': win32com,
            'pythoncom': pythoncom,
            'math': math,
            'os': os_module,
            'json': json,
            'FinalAnswer': _final_answer,
            'result': None,
        })
        
        # Capture stdout and stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        
        try:
            sys.stdout = captured_stdout
            sys.stderr = captured_stderr
            try:
                exec(code, _python_exec_ns)
            except _PythonFinalAnswerSignal:
                pass
        except Exception:
            script_failed = True
            raise
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            if auto_transaction and doc is not None:
                try:
                    end_undo = getattr(doc, "EndUndoRecord", None)
                    if callable(end_undo):
                        end_undo()
                    # Undo only if the failed script really changed the document; otherwise
                    # EditUndo2 would revert the user's previous, unrelated operation.
                    if script_failed and _doc_fingerprint(doc) != (vol_before, feat_count_before):
                        undo_fn = getattr(doc, "EditUndo2", None)
                        if callable(undo_fn):
                            undo_fn(1)
                except Exception:
                    pass

        # Enforce read_only fingerprint if requested
        doc_after = sw_automation.app.ActiveDoc if sw_automation.app else None
        vol_after = capture_vol_state(doc_after) if doc_after is not None else None
        feat_count_after = None
        if doc_after is not None:
            try:
                fm_after = getattr(doc_after, "FeatureManager", None)
                if fm_after is not None:
                    feat_count_after = fm_after.GetFeatureCount(True)
            except Exception:
                pass

        if read_only and (vol_before != vol_after or feat_count_before != feat_count_after):
            if doc_after is not None:
                try:
                    undo_fn = getattr(doc_after, "EditUndo2", None)
                    if callable(undo_fn):
                        undo_fn(1)
                except Exception:
                    pass
            return {
                "success": False,
                "message": (
                    f"Read-only violation in execute_python: document state changed "
                    f"(vol: {vol_before} -> {vol_after}, features: {feat_count_before} -> {feat_count_after}). Rolled back."
                ),
                "error_code": int(SwErrors.swInvalidCall),
                "error_name": "swInvalidCall",
                "timestamp": datetime.datetime.now().isoformat(),
                "data": {"read_only_violation": True, "vol_before": vol_before, "vol_after": vol_after},
            }

        # Get captured output
        stdout_text = captured_stdout.getvalue()
        stderr_text = captured_stderr.getvalue()
        result_val = final_answer_box["value"] if final_answer_box["called"] else _python_exec_ns.get('result')
        
        # Build response message
        message_parts = []
        
        if stdout_text:
            message_parts.append(f"=== Output ===\n{stdout_text.rstrip()}")
        
        if stderr_text:
            message_parts.append(f"=== Stderr ===\n{stderr_text.rstrip()}")
        
        if result_val is not None:
            message_parts.append(f"=== Result ===\n{result_val}")
        
        if not message_parts:
            message_parts.append("Code executed successfully (no output)")
        
        return {
            "success": True,
            "message": "\n\n".join(message_parts),
            "error_code": 0,
            "error_name": "swSuccess",
            "timestamp": datetime.datetime.now().isoformat(),
            "data": {
                "stdout": stdout_text,
                "stderr": stderr_text,
                "result": str(result_val) if result_val is not None else None,
                "final_answer_called": final_answer_box["called"],
            }
        }
        
    except SyntaxError as e:
        return {
            "success": False,
            "message": f"Syntax error: {e}",
            "error_code": int(SwErrors.swInvalidInput),
            "error_name": "swInvalidInput",
            "timestamp": datetime.datetime.now().isoformat(),
            "data": {"error_type": "SyntaxError", "details": str(e)}
        }
        
    except Exception as e:
        tb = traceback.format_exc()
        return {
            "success": False,
            "message": f"Execution error: {e}\n\nTraceback:\n{tb}",
            "error_code": int(SwErrors.swGeneralError),
            "error_name": "swGeneralError",
            "timestamp": datetime.datetime.now().isoformat(),
            "data": {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "traceback": tb
            }
        }


# ============================================================================
# FIXED: list_features
# ============================================================================

def _list_features_fixed() -> Dict:
    """
    List all features in the active document.
    FIXED v4.1: Property access for SW 2025 (FirstFeature, GetNextFeature, GetTypeName2).
    """
    try:
        doc, err = sw_automation.get_active_doc()
        if err:
            return err
        
        features = []
        
        # FIXED: FirstFeature is a property in SW 2025 COM, not a method
        feat = get_object_member(doc, "FirstFeature")
        
        while feat is not None:
            try:
                name = ""
                feat_type = ""
                suppressed = False
                
                try:
                    name = feat.Name
                except Exception:
                    name = "<unknown>"
                
                # FIXED: GetTypeName2 is a property in SW 2025 COM
                try:
                    feat_type = feat.GetTypeName2
                    if callable(feat_type):
                        feat_type = feat_type()
                except Exception:
                    try:
                        feat_type = feat.GetTypeName()
                    except Exception:
                        feat_type = "<unknown>"
                
                try:
                    suppressed = feat.IsSuppressed()
                except Exception:
                    suppressed = False
                
                features.append({
                    "name": name,
                    "type": feat_type,
                    "suppressed": bool(suppressed)
                })

                # Iterate subfeatures (absorbed sketches, reference geometry, etc.)
                try:
                    subfeat = getattr(feat, "GetFirstSubFeature", None)
                    if callable(subfeat):
                        subfeat = subfeat()
                    while subfeat is not None:
                        try:
                            s_name = getattr(subfeat, "Name", "<unknown>")
                            s_type = getattr(subfeat, "GetTypeName2", None)
                            if callable(s_type):
                                s_type = s_type()
                            elif s_type is None:
                                s_type = getattr(subfeat, "GetTypeName", lambda: "<unknown>")()
                            s_supp = False
                            try:
                                s_supp = subfeat.IsSuppressed()
                            except Exception:
                                pass
                            features.append({
                                "name": s_name,
                                "type": str(s_type),
                                "suppressed": bool(s_supp),
                                "parent": name,
                            })
                        except Exception:
                            pass
                        next_sub = getattr(subfeat, "GetNextSubFeature", None)
                        subfeat = next_sub() if callable(next_sub) else next_sub
                except Exception:
                    pass
                
            except Exception as e:
                features.append({
                    "name": "<error>",
                    "type": str(e),
                    "suppressed": False
                })
            
            # FIXED: GetNextFeature is a property in SW 2025 COM
            try:
                feat = get_object_member(feat, "GetNextFeature")
            except Exception:
                break
        
        # One line per feature; the empty system folders every document has are only counted.
        system_types = {
            "CommentsFolder", "FavoriteFolder", "HistoryFolder", "SelectionSetFolder", "SensorFolder",
            "DocsFolder", "DetailCabinet", "InkMarkupFolder", "SurfaceBodyFolder", "SolidBodyFolder",
            "EnvFolder", "EqnFolder", "ConfigTableFolder", "AmbientLight", "DirectionLight",
        }
        shown = [f for f in features
                 if f["type"] not in system_types and f.get("parent") not in ("Lights and Cameras",)]
        lines = []
        for f in shown:
            line = f"{'  ' if f.get('parent') else ''}{f['name']} [{f['type']}]"
            if f.get("suppressed"):
                line += " (suppressed)"
            lines.append(line)
        hidden = len(features) - len(shown)
        return {
            "success": True,
            "message": f"{len(shown)} features (plus {hidden} standard system folders/lights not listed; "
                       "indented = sub-feature of the one above):\n" + "\n".join(lines),
            "error_code": 0,
            "error_name": "swSuccess",
            "data": {"count": len(shown), "system_features_hidden": hidden}
        }
        
    except Exception as e:
        logger.error(f"List features error: {e}\n{traceback.format_exc()}")
        return {
            "success": False,
            "message": f"Error: {e}",
            "error_code": int(SwErrors.swGeneralError),
            "error_name": "swGeneralError",
            "data": {}
        }

# ============================================================================
# NEW: close_sketch handler
# ============================================================================

def _close_sketch_handler() -> Dict:
    """
    Close the active sketch if one is open.
    Returns sketch status info.
    """
    try:
        doc, err = sw_automation.get_active_doc()
        if err:
            return err

        dtype = sw_automation.get_document_type(doc)
        if dtype == "Drawing":
            return {
                "success": False,
                "message": "close_sketch is not supported for Drawing documents",
                "error_code": int(SwErrors.swInvalidDocumentType),
                "error_name": "swInvalidDocumentType",
                "data": {"document_type": dtype}
            }
        
        # Check if sketch is active
        had_active = False
        try:
            act = get_attr_or_call(doc, "GetActiveSketch2")
            if act is not None:
                had_active = True
            else:
                active_sketch = getattr(doc.SketchManager, "ActiveSketch", None)
                if active_sketch is not None and getattr(active_sketch, "IsSketchEditable", False):
                    had_active = True
        except Exception:
            pass
        
        if had_active:
            try:
                doc.InsertSketch2(True)
            except Exception:
                try:
                    doc.SketchManager.InsertSketch(True)
                except Exception:
                    pass
            
            # Post-condition check: verify sketch mode is actually closed
            still_active = False
            try:
                act = get_attr_or_call(doc, "GetActiveSketch2")
                if act is not None:
                    still_active = True
                else:
                    active_sketch = getattr(doc.SketchManager, "ActiveSketch", None)
                    if active_sketch is not None and getattr(active_sketch, "IsSketchEditable", False):
                        still_active = True
            except Exception:
                pass

            if still_active:
                return {
                    "success": False,
                    "message": "Post-condition failed: Sketch is still active after close_sketch",
                    "error_code": int(SwErrors.swSketchError),
                    "error_name": "swSketchError",
                    "data": {"had_active_sketch": True, "action": "failed"}
                }

            return {
                "success": True,
                "message": "Sketch closed successfully",
                "error_code": 0,
                "error_name": "swSuccess",
                "data": {"had_active_sketch": True, "action": "closed"}
            }
        else:
            return {
                "success": False,
                "message": "no active sketch",
                "error_code": int(SwErrors.swInvalidCall),
                "error_name": "swInvalidCall",
                "data": {"had_active_sketch": False, "action": "none"}
            }
        
    except Exception as e:
        logger.error(f"Close sketch error: {e}\n{traceback.format_exc()}")
        return {
            "success": False,
            "message": f"Error: {e}",
            "error_code": int(SwErrors.swGeneralError),
            "error_name": "swGeneralError",
            "data": {"traceback": traceback.format_exc()}
        }


# ============================================================================
# NEW: get_sketch_status handler
# ============================================================================

def _get_sketch_status_handler() -> Dict:
    """
    Get diagnostic info about the current sketch state.
    Useful for debugging sketch/extrude issues.
    """
    try:
        doc, err = sw_automation.get_active_doc()
        if err:
            return err
        
        info = {
            "has_active_sketch": False,
            "active_sketch_name": None,
            "sketch_count": 0,
            "sketch_names": [],
            "feature_count": 0,
            "extrusion_count": 0,
        }
        
        # Check active sketch (ignore drawing sheet background sketch)
        dtype = sw_automation.get_document_type(doc)
        if dtype != "Drawing":
            try:
                act = get_attr_or_call(doc, "GetActiveSketch2")
                active_sketch = getattr(doc.SketchManager, "ActiveSketch", None)
                is_editable = getattr(active_sketch, "IsSketchEditable", False) if active_sketch else False
                if act is not None or is_editable:
                    info["has_active_sketch"] = True
                    target = act or active_sketch
                    try:
                        info["active_sketch_name"] = getattr(target, "Name", "<unknown>")
                    except Exception:
                        info["active_sketch_name"] = "<unknown>"
            except Exception:
                pass
        
        # Walk feature tree (using PROPERTIES not methods)
        try:
            feat = doc.FirstFeature
            while feat is not None:
                try:
                    feat_type = get_attr_or_call(feat, "GetTypeName2")
                    info["feature_count"] += 1
                    
                    if feat_type == "ProfileFeature":
                        info["sketch_count"] += 1
                        info["sketch_names"].append(feat.Name)
                    elif feat_type == "Extrusion":
                        info["extrusion_count"] += 1
                except Exception:
                    pass
                try:
                    feat = feat.GetNextFeature
                except Exception:
                    break
        except Exception:
            pass
        
        # Build readable message
        status = "OPEN" if info["has_active_sketch"] else "CLOSED"
        msg = (f"Sketch status: {status}. "
               f"Sketches: {info['sketch_count']} {info['sketch_names']}. "
               f"Extrusions: {info['extrusion_count']}. "
               f"Total features: {info['feature_count']}")
        
        return {
            "success": True,
            "message": msg,
            "error_code": 0,
            "error_name": "swSuccess",
            "data": info
        }
        
    except Exception as e:
        logger.error(f"Sketch status error: {e}\n{traceback.format_exc()}")
        return {
            "success": False,
            "message": f"Error: {e}",
            "error_code": int(SwErrors.swGeneralError),
            "error_name": "swGeneralError",
            "data": {"traceback": traceback.format_exc()}
        }


# ============================================================================
# Main Entry Point
# ============================================================================


# ============================================================================
# Interactive Studio (A2UI surface served through ui/bridge.py)
# ============================================================================

def _studio_action_handler(arguments: dict) -> Dict[str, Any]:
    """Apply one A2UI renderer-to-agent action sent by the studio view through the MCP host."""
    from .ui import bridge

    message = arguments.get("message") or {}
    action = message.get("action") if isinstance(message, dict) else None
    if not isinstance(action, dict) or not isinstance(action.get("name"), str):
        return sw_automation._result(False, "message.action.name is required", SwErrors.swInvalidInput)
    bridge.set_automation_instance(sw_automation)
    messages = bridge.handle_action(action)
    return _ok(bridge.get_session().status, {"a2ui": {"messages": messages}})


def _show_interactive_studio_handler(arguments: dict) -> Dict[str, Any]:
    """
    Describe the active model as an A2UI surface (dimension sliders + viewport) and
    publish it through the local studio bridge.
    """
    try:
        from .ui import a2ui, bridge

        doc, err = sw_automation.get_active_doc()
        if err:
            return err

        bridge.set_automation_instance(sw_automation)
        # The bridge only backs the open-in-browser link. In the Claude app the studio is
        # shown inline and talks to this server through the host, so a bridge failure is not fatal.
        try:
            bridge.start_bridge()
            studio_url = bridge.get_studio_url()
        except Exception as e:
            logger.warning(f"Studio bridge unavailable: {e}")
            studio_url = None

        try:
            model_title = str(get_attr_or_call(doc, "GetTitle") or "SolidWorks Model")
        except Exception:
            model_title = "SolidWorks Model"

        dims_res = sw_automation.get_dimensions(feature_name=arguments.get("feature_filter"))
        raw_dims: Any = []
        if isinstance(dims_res, dict) and dims_res.get("success"):
            raw_dims = dims_res.get("data") or []
        dims = a2ui.build_dimension_model(
            raw_dims,
            feature_labels=arguments.get("feature_labels") or {},
            dimension_labels=arguments.get("dimension_labels") or {},
        )

        view_name = arguments.get("view_name", "*Isometric")
        session = bridge.set_session(
            title=model_title,
            dims=dims,
            image=bridge.capture_viewport_data_uri(view_name),
            bbox=bridge.model_bounds(),
            view=view_name,
        )

        lines = [
            f"Interactive studio ready for `{model_title}` with {len(dims)} adjustable dimension(s).",
            (f"If it is not shown inline, open it in a browser: {studio_url}" if studio_url
             else "It is shown inline in clients that support MCP Apps (no browser link is available)."),
            "",
        ]
        for dim in list(dims.values())[:10]:
            lines.append(f"- {dim['label']} ({dim['name']}) on {dim['feature']}: {dim['value']:g} {dim['units']}")
        if len(dims) > 10:
            lines.append(f"- ...and {len(dims) - 10} more")
        if not dims:
            lines.append("No driving dimensions were found.")

        return {
            "success": True,
            "message": "\n".join(lines),
            "error_code": 0,
            "error_name": "swSuccess",
            "data": {
                "modelTitle": model_title,
                "studioUrl": studio_url,
                "dimensions": [
                    {k: d[k] for k in ("name", "label", "feature", "value", "units")} for d in dims.values()
                ],
                # A2UI 1.0 payload consumed by the studio view (and by any other A2UI renderer).
                "a2ui": {"messages": session.messages()},
            },
        }
    except Exception as e:
        logger.error(f"Error in show_interactive_studio: {e}\n{traceback.format_exc()}")
        return {
            "success": False,
            "message": f"Failed to launch interactive studio: {e}",
            "error_code": int(SwErrors.swGeneralError),
            "error_name": "swGeneralError",
            "data": {"traceback": traceback.format_exc()},
        }


async def main():
    """Serve MCP over stdio."""
    logger.info(f"Starting SolidWorks MCP Server v{__version__} (log: {LOG_FILE or 'stderr'})")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run():
    """Console-script entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
