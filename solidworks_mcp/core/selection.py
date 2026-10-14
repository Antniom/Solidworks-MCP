"""
Declarative Selection Resolver
------------------------------
Provides robust, reproducible entity selection across geometry changes.
Replaces brittle index/state-based selection with declarative targets:
- By semantic name ("Front Plane", "Boss-Extrude1")
- By 3D coordinate point with explicit units
- By topology and geometric filtering (e.g. normal vector, highest planar face, cylindrical faces)
- With explicit selection marks for multi-entity operations (e.g. mate profiles, fillets)
"""

import math
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import win32com.client
    import pythoncom
    HAS_PYWIN32 = True
except ImportError:
    HAS_PYWIN32 = False

from solidworks_mcp.core.com_compat import get_attr_or_call


@dataclass
class SelectionTarget:
    """Declarative specification for an entity to select."""
    target_type: str = "face"  # plane, feature, face, edge, vertex, body
    name: Optional[str] = None
    point: Optional[List[float]] = None  # [x, y, z]
    unit: str = "mm"
    normal: Optional[List[float]] = None  # [nx, ny, nz] for planar faces
    mark: int = 0
    append: bool = False
    direction: Optional[str] = None  # top, bottom, front, back, left, right


@dataclass
class SelectionResult:
    """Outcome of a declarative selection operation."""
    success: bool
    target_type: str
    matched_name: Optional[str] = None
    mark: int = 0
    point_m: Optional[List[float]] = None
    message: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "target_type": self.target_type,
            "matched_name": self.matched_name,
            "mark": self.mark,
            "point_m": self.point_m,
            "message": self.message,
            "error": self.error,
        }


class DeclarativeSelectionResolver:
    """Resolves high-level declarative selection targets to live COM selections."""

    PLANE_MAP = {
        "front": "Front Plane",
        "top": "Top Plane",
        "right": "Right Plane",
        "front plane": "Front Plane",
        "top plane": "Top Plane",
        "right plane": "Right Plane",
    }

    DIRECTION_NORMALS = {
        "top": (0.0, 1.0, 0.0),
        "+y": (0.0, 1.0, 0.0),
        "y": (0.0, 1.0, 0.0),
        "bottom": (0.0, -1.0, 0.0),
        "-y": (0.0, -1.0, 0.0),
        "front": (0.0, 0.0, 1.0),
        "+z": (0.0, 0.0, 1.0),
        "z": (0.0, 0.0, 1.0),
        "back": (0.0, 0.0, -1.0),
        "-z": (0.0, 0.0, -1.0),
        "right": (1.0, 0.0, 0.0),
        "+x": (1.0, 0.0, 0.0),
        "x": (1.0, 0.0, 0.0),
        "left": (-1.0, 0.0, 0.0),
        "-x": (-1.0, 0.0, 0.0),
    }

    @classmethod
    def resolve_and_select(
        cls,
        doc: Any,
        target: SelectionTarget,
        unit_converter: Optional[Any] = None,
    ) -> SelectionResult:
        """
        Execute declarative selection against the active document.
        """
        if doc is None:
            return SelectionResult(
                success=False,
                target_type=target.target_type,
                error="No active document provided for selection.",
            )

        if not target.append:
            try:
                doc.ClearSelection2(True)
            except Exception as e:
                logger.debug(f"ClearSelection2 warning: {e}")

        # 1. Plane selection
        if target.target_type.lower() in ("plane", "refplane"):
            return cls._select_plane(doc, target)

        # 2. Feature selection by name
        if target.target_type.lower() == "feature":
            return cls._select_feature(doc, target)

        # 3. Coordinate point selection (Face, Edge, Vertex)
        if target.point is not None:
            return cls._select_by_point(doc, target, unit_converter)

        # 4. Face selection by filter (direction / normal)
        if target.target_type.lower() == "face" and (target.direction or target.normal):
            return cls._select_face_by_filter(doc, target)

        # 5. Direct name fallback
        if target.name:
            return cls._select_by_name(doc, target)

        return SelectionResult(
            success=False,
            target_type=target.target_type,
            error="Target must specify 'name', 'point', or 'direction/normal'.",
        )

    @classmethod
    def _select_plane(cls, doc: Any, target: SelectionTarget) -> SelectionResult:
        raw_name = (target.name or "front").strip().lower()
        plane_name = cls.PLANE_MAP.get(raw_name, target.name or "Front Plane")

        null_callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None) if HAS_PYWIN32 else None
        try:
            ok = doc.Extension.SelectByID2(
                plane_name, "PLANE", 0.0, 0.0, 0.0, target.append, target.mark, null_callout, 0
            )
            if ok:
                return SelectionResult(
                    success=True,
                    target_type="plane",
                    matched_name=plane_name,
                    mark=target.mark,
                    message=f"Selected plane '{plane_name}'",
                )
            return SelectionResult(
                success=False,
                target_type="plane",
                matched_name=plane_name,
                error=f"SelectByID2 failed for plane '{plane_name}'",
            )
        except Exception as e:
            return SelectionResult(
                success=False,
                target_type="plane",
                matched_name=plane_name,
                error=f"Plane selection exception: {e}",
            )

    @classmethod
    def _select_feature(cls, doc: Any, target: SelectionTarget) -> SelectionResult:
        if not target.name:
            return SelectionResult(
                success=False,
                target_type="feature",
                error="Feature selection requires target 'name'.",
            )

        null_callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None) if HAS_PYWIN32 else None
        try:
            ok = doc.Extension.SelectByID2(
                target.name, "BODYFEATURE", 0.0, 0.0, 0.0, target.append, target.mark, null_callout, 0
            )
            if ok:
                return SelectionResult(
                    success=True,
                    target_type="feature",
                    matched_name=target.name,
                    mark=target.mark,
                    message=f"Selected feature '{target.name}'",
                )
            return SelectionResult(
                success=False,
                target_type="feature",
                matched_name=target.name,
                error=f"SelectByID2 failed for feature '{target.name}'",
            )
        except Exception as e:
            return SelectionResult(
                success=False,
                target_type="feature",
                matched_name=target.name,
                error=f"Feature selection exception: {e}",
            )

    @classmethod
    def _select_by_point(
        cls,
        doc: Any,
        target: SelectionTarget,
        unit_converter: Optional[Any] = None,
    ) -> SelectionResult:
        # Convert coords to meters
        coords = list(target.point)
        scale = 0.001  # default mm to meters
        unit_str = (target.unit or "mm").lower()
        if unit_str == "inch":
            scale = 0.0254
        elif unit_str in ("m", "meter"):
            scale = 1.0
        elif unit_str == "cm":
            scale = 0.01

        xm, ym, zm = [float(c) * scale for c in coords[:3]]
        type_str = target.target_type.upper()
        if type_str not in ("FACE", "EDGE", "VERTEX", "BODY"):
            type_str = "FACE"

        null_callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None) if HAS_PYWIN32 else None
        try:
            ok = doc.Extension.SelectByID2(
                "", type_str, xm, ym, zm, target.append, target.mark, null_callout, 0
            )
            if ok:
                return SelectionResult(
                    success=True,
                    target_type=target.target_type,
                    point_m=[xm, ym, zm],
                    mark=target.mark,
                    message=f"Selected {type_str} at point ({xm*1000:.2f}, {ym*1000:.2f}, {zm*1000:.2f}) mm",
                )

            # Fallback: scan solid bodies for a face containing or closest to this point
            if type_str == "FACE":
                bodies = doc.GetBodies2(0, False) if callable(getattr(doc, "GetBodies2", None)) else getattr(doc, "GetBodies2", None)
                if bodies:
                    best_f = None
                    best_dist = float("inf")
                    for body in bodies:
                        faces = body.GetFaces() if callable(getattr(body, "GetFaces", None)) else getattr(body, "GetFaces", None)
                        if not faces:
                            continue
                        for f in faces:
                            box = getattr(f, "GetBox", None)
                            if callable(box):
                                box = box()
                            if box and len(box) >= 6:
                                dx = max(box[0] - xm, 0, xm - box[3])
                                dy = max(box[1] - ym, 0, ym - box[4])
                                dz = max(box[2] - zm, 0, zm - box[5])
                                d = dx * dx + dy * dy + dz * dz
                                if d < best_dist:
                                    best_dist = d
                                    best_f = f
                    if best_f and best_dist < 1e-4:
                        if best_f.Select2(target.append, target.mark):
                            return SelectionResult(
                                success=True,
                                target_type=target.target_type,
                                point_m=[xm, ym, zm],
                                mark=target.mark,
                                message=f"Selected face near point ({xm*1000:.2f}, {ym*1000:.2f}, {zm*1000:.2f}) mm via geometry fallback",
                            )

            return SelectionResult(
                success=False,
                target_type=target.target_type,
                point_m=[xm, ym, zm],
                error=f"No {type_str} intersected ray at point ({xm*1000:.2f}, {ym*1000:.2f}, {zm*1000:.2f}) mm",
            )
        except Exception as e:
            return SelectionResult(
                success=False,
                target_type=target.target_type,
                point_m=[xm, ym, zm],
                error=f"Point selection exception: {e}",
            )

    @classmethod
    def _select_face_by_filter(cls, doc: Any, target: SelectionTarget) -> SelectionResult:
        """Finds face matching direction/normal vector across solid bodies and selects it."""
        desired_normal = None
        if target.direction and target.direction.lower() in cls.DIRECTION_NORMALS:
            desired_normal = cls.DIRECTION_NORMALS[target.direction.lower()]
        elif target.normal and len(target.normal) >= 3:
            mag = math.sqrt(sum(c * c for c in target.normal[:3]))
            if mag > 1e-6:
                desired_normal = tuple(c / mag for c in target.normal[:3])

        if not desired_normal:
            return SelectionResult(
                success=False,
                target_type="face",
                error="Invalid direction or normal vector specified.",
            )

        try:
            bodies = doc.GetBodies2(0, False)
            if not bodies:
                return SelectionResult(
                    success=False,
                    target_type="face",
                    error="No solid bodies found in active model.",
                )

            best_face = None
            best_pos_score = -float("inf")
            best_center = None

            for body in bodies:
                faces = None
                try:
                    faces = get_attr_or_call(body, "GetFaces")
                except Exception:
                    continue
                if not faces:
                    continue

                for face in faces:
                    try:
                        surface_obj = get_attr_or_call(face, "GetSurface")
                    except Exception:
                        continue
                    if surface_obj is None:
                        continue

                    # Check planar
                    try:
                        is_plane = get_attr_or_call(surface_obj, "IsPlane")
                    except Exception:
                        continue
                    if not is_plane:
                        continue

                    # Planar normal - use GetPlaneParams() with PlaneParams fallback
                    params = None
                    try:
                        params = get_attr_or_call(surface_obj, "GetPlaneParams")
                    except Exception:
                        try:
                            params = get_attr_or_call(surface_obj, "PlaneParams")
                        except Exception:
                            params = None

                    if not params or len(params) < 3:
                        continue

                    normal = (params[0], params[1], params[2])
                    # Planar face normal is parallel to desired normal if abs(dot) > 0.9
                    parallel_dot = sum(a * b for a, b in zip(normal, desired_normal))
                    if abs(parallel_dot) > 0.9:
                        try:
                            box = get_attr_or_call(face, "GetBox")
                            if box and len(box) >= 6:
                                center = [(box[0] + box[3]) / 2, (box[1] + box[4]) / 2, (box[2] + box[5]) / 2]
                                pos_score = sum(c * d for c, d in zip(center, desired_normal))
                                if pos_score > best_pos_score:
                                    best_pos_score = pos_score
                                    best_face = face
                                    best_center = center
                        except Exception:
                            pass

            if best_face is not None:
                # Select the face directly
                ok = best_face.Select2(target.append, target.mark)
                if ok:
                    return SelectionResult(
                        success=True,
                        target_type="face",
                        matched_name="PlanarFace",
                        mark=target.mark,
                        point_m=best_center,
                        message=f"Selected planar face matching direction '{target.direction or target.normal}'",
                    )
                return SelectionResult(
                    success=False,
                    target_type="face",
                    error="Face found but Select2() returned False.",
                )

            return SelectionResult(
                success=False,
                target_type="face",
                error=f"No planar face matched normal {desired_normal}.",
            )
        except Exception as e:
            return SelectionResult(
                success=False,
                target_type="face",
                error=f"Face filter selection exception: {e}",
            )

    @classmethod
    def _select_by_name(cls, doc: Any, target: SelectionTarget) -> SelectionResult:
        null_callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None) if HAS_PYWIN32 else None
        type_str = target.target_type.upper()
        try:
            ok = doc.Extension.SelectByID2(
                target.name, type_str, 0.0, 0.0, 0.0, target.append, target.mark, null_callout, 0
            )
            if ok:
                return SelectionResult(
                    success=True,
                    target_type=target.target_type,
                    matched_name=target.name,
                    mark=target.mark,
                    message=f"Selected {type_str} '{target.name}'",
                )
            return SelectionResult(
                success=False,
                target_type=target.target_type,
                matched_name=target.name,
                error=f"SelectByID2 failed for {type_str} '{target.name}'",
            )
        except Exception as e:
            return SelectionResult(
                success=False,
                target_type=target.target_type,
                matched_name=target.name,
                error=f"Selection exception: {e}",
            )
