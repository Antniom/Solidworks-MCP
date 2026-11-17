"""
SolidWorks Sketch Operations
----------------------------
Create sketches and draw 2D geometry.
"""

import math
import logging
import traceback
from typing import Optional, Dict, List, Tuple

import win32com.client
import pythoncom

from ..constants import SwErrors, SwPlanes
from ..core.sketch_guard import (
    TOL_M, arc_center_radius, axis_note, close, delete_segments, direct_sketch, line_points,
    mm, sketch_frame, sketch_to_world_mm,
)

logger = logging.getLogger(__name__)

_NOT_CREATED = (
    "SolidWorks did not create the {what} (the API returned nothing). With snapping disabled this "
    "usually means degenerate geometry (zero length/size) or that the sketch is not editable."
)


class SketchOperations:
    """
    Mixin class for sketch operations
    
    Requires parent class to have:
    - get_active_doc(): Document access method
    - _result(): Result factory method
    - _units: UnitConverter instance
    """
    
    def create_sketch(
        self,
        plane: str = "Front",
        offset: float = 0.0,
        face_index: Optional[int] = None,
        unit: Optional[str] = None,
    ) -> Dict:
        """
        Create a new sketch on a specified plane, an offset reference plane, or a body face by 1-based face_index.
        
        Args:
            plane: Plane name - "Front" ("XY"), "Top" ("XZ"), or "Right" ("YZ")
            offset: Optional offset distance from plane (creates an offset RefPlane automatically)
            face_index: Optional 1-based face index on solid body 1
            unit: Unit for offset (default active unit, usually mm)
        
        Returns:
            Result dictionary
        """
        try:
            from ..core.com_compat import get_attr_or_call

            doc, err = self.get_active_doc()
            if err:
                return err

            empty_callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

            if face_index is not None:
                bodies = get_attr_or_call(doc, "GetBodies2", 0, False) or []
                if not bodies:
                    return self._result(False, "No solid bodies found for face_index selection.", SwErrors.swSelectionError)
                faces = get_attr_or_call(bodies[0], "GetFaces") or []
                if face_index < 1 or face_index > len(faces):
                    return self._result(
                        False,
                        f"face_index={face_index} out of range (1..{len(faces)})",
                        SwErrors.swInvalidParameter,
                    )
                doc.ClearSelection2(True)
                faces[face_index - 1].Select2(False, 0)
                doc.InsertSketch2(True)
                return self._sketch_created_result(doc, f"face [{face_index}]", {"face_index": face_index})

            plane_map = {
                "front": "Front Plane",
                "top": "Top Plane",
                "right": "Right Plane",
                "front plane": "Front Plane",
                "top plane": "Top Plane",
                "right plane": "Right Plane",
                "xy": "Front Plane",
                "xz": "Top Plane",
                "yz": "Right Plane",
            }
            plane_key = (plane or "").strip().lower()
            if plane_key not in plane_map:
                return self._result(
                    False,
                    f"Invalid plane '{plane}'. Valid planes are: Front (XY), Top (XZ), Right (YZ)",
                    SwErrors.swInvalidParameter,
                )
            plane_name = plane_map[plane_key]
            target_plane_label = plane_name

            # Select base plane
            doc.ClearSelection2(True)
            result = doc.Extension.SelectByID2(
                plane_name, "PLANE", 0, 0, 0, False, 0, empty_callout, 0
            )
            if not result:
                return self._result(False, f"Could not select {plane_name}", SwErrors.swSelectionError)

            # Optional offset plane creation
            ref_plane = None
            if abs(float(offset or 0.0)) > 1e-9:
                unit_str = unit or self._units.default_unit.value
                offset_m = abs(self._units.to_meters(float(offset), unit))
                # swRefPlaneReferenceConstraint_Distance = 8, Flip = 256 (8 | 256 = 264 when negative)
                constraint_flag = 8 if float(offset) > 0 else (8 | 256)
                ref_plane = doc.FeatureManager.InsertRefPlane(constraint_flag, offset_m, 0, 0, 0, 0)
                if ref_plane is None:
                    return self._result(
                        False,
                        f"Failed to create offset reference plane ({offset}{unit_str} from {plane_name})",
                        SwErrors.swFeatureError,
                    )
                ref_plane_name = getattr(ref_plane, "Name", None)
                if ref_plane_name:
                    doc.ClearSelection2(True)
                    doc.Extension.SelectByID2(ref_plane_name, "PLANE", 0, 0, 0, False, 0, empty_callout, 0)
                target_plane_label = f"{plane_name} ({offset:+g}{unit_str} offset -> {ref_plane_name or 'RefPlane'})"

            # Insert sketch
            doc.InsertSketch2(True)
            
            # Post-condition check: verify active sketch mode was entered
            is_active = False
            try:
                sk_mgr = getattr(doc, "SketchManager", None)
                if sk_mgr is not None and getattr(sk_mgr, "ActiveSketch", None) is not None:
                    is_active = True
            except Exception:
                pass
            if not is_active:
                try:
                    if get_attr_or_call(doc, "GetSketchMode"):
                        is_active = True
                except Exception:
                    pass
            
            if not is_active:
                return self._result(
                    False,
                    f"Post-condition failed: Active sketch mode was not entered on {target_plane_label}",
                    SwErrors.swSketchError,
                )

            extra = {"plane": target_plane_label, "offset": offset}
            if ref_plane is not None:
                # The auto-created plane is drawn larger than the part and zoom-to-fit included it,
                # so later sketch work ran at mm-per-pixel scale (MCP-008). Hide it.
                try:
                    doc.ClearSelection2(True)
                    if ref_plane.Select2(False, 0):
                        doc.BlankRefGeom()
                    doc.ClearSelection2(True)
                    extra["offset_plane"] = getattr(ref_plane, "Name", None)
                    extra["offset_plane_hidden"] = True
                except Exception as hide_ex:
                    logger.debug(f"could not hide offset plane: {hide_ex}")
            return self._sketch_created_result(doc, target_plane_label, extra)
            
        except Exception as e:
            logger.error(f"Create sketch error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
    
    def _sketch_created_result(self, doc, label: str, extra: Dict) -> Dict:
        """Success result for a new sketch, including where its axes point in world coordinates."""
        data = dict(extra)
        data["sketch_active"] = True
        frame = sketch_frame(doc)
        msg = f"Sketch created on {label}"
        if frame:
            data["sketch_frame"] = frame
            note = axis_note(frame)
            data["sketch_axes"] = note
            # Draw tools take sketch-local x/y. Say so loudly when that differs from world X/Y.
            if frame["x_axis_world"] != [1.0, 0.0, 0.0] or frame["y_axis_world"] != [0.0, 1.0, 0.0]:
                msg += f". Draw coordinates are sketch-local: {note}"
            if any(abs(c) > 1e-6 for c in frame["origin_mm"]):
                msg += f"; sketch origin at world {frame['origin_mm']} mm"
        return self._result(True, msg, SwErrors.swSuccess, data)

    def create_sketch_on_face(self, x: float = 0, y: float = 0, z: float = 0,
                              unit: str = None) -> Dict:
        """
        Create a new sketch on a face selected by coordinate.
        ADDED v4.1: Enables cut-extrude on existing body faces (not just ref planes).
        
        Args:
            x, y, z: Coordinates (in user units) of a point ON the target face
            unit: Unit for coordinates (uses default if None)
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            
            # Convert to meters
            x_m = self._units.to_meters(x, unit)
            y_m = self._units.to_meters(y, unit)
            z_m = self._units.to_meters(z, unit)
            unit_str = unit or self._units.default_unit.value
            
            # Clear any selection
            doc.ClearSelection2(True)

            # Find the face geometrically: the face whose closest point to (x, y, z) is within
            # 0.01 mm. SelectByID2 with a point is a screen pick and missed a point lying exactly
            # on a 1 mm plate's face (MCP-006); prefer planar faces on ties.
            from ..core.com_compat import get_attr_or_call
            best, best_d, best_planar = None, None, False
            for body in (get_attr_or_call(doc, "GetBodies2", 0, False) or []):
                for f in (get_attr_or_call(body, "GetFaces") or []):
                    try:
                        cp = f.GetClosestPointOn(x_m, y_m, z_m)
                        if not cp or len(cp) < 3:
                            continue
                        d = math.dist((cp[0], cp[1], cp[2]), (x_m, y_m, z_m))
                        if d > 1e-5:
                            continue
                        surf = get_attr_or_call(f, "GetSurface")
                        planar = bool(get_attr_or_call(surf, "IsPlane")) if surf is not None else False
                        if best is None or d < best_d - 1e-9 or (abs(d - best_d) <= 1e-9 and planar and not best_planar):
                            best, best_d, best_planar = f, d, planar
                    except Exception:
                        continue
            selected = bool(best.Select2(False, 0)) if best is not None else False
            if not selected:
                # Fall back to the old screen pick (works for surfaces not on solid bodies).
                empty_callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
                selected = doc.Extension.SelectByID2("", "FACE", x_m, y_m, z_m, False, 0, empty_callout, 0)

            if not selected:
                return self._result(False,
                    f"No face found at ({x}{unit_str}, {y}{unit_str}, {z}{unit_str}). "
                    f"Coordinates must be on an existing body face (within 0.01 mm).",
                    SwErrors.swSelectionError)
            
            # Insert sketch on the selected face
            doc.InsertSketch2(True)

            # Post-condition check: verify active sketch mode was entered
            is_active = False
            try:
                sk_mgr = getattr(doc, "SketchManager", None)
                if sk_mgr is not None and getattr(sk_mgr, "ActiveSketch", None) is not None:
                    is_active = True
            except Exception:
                pass
            if not is_active:
                try:
                    from ..core.com_compat import get_attr_or_call
                    if get_attr_or_call(doc, "GetSketchMode"):
                        is_active = True
                except Exception:
                    pass

            if not is_active:
                return self._result(
                    False,
                    f"Post-condition failed: Active sketch mode was not entered on selected face",
                    SwErrors.swSketchError,
                )
            
            return self._sketch_created_result(
                doc, f"face at ({x}, {y}, {z}) {unit_str}",
                {"face_point": {"x": x, "y": y, "z": z}, "unit": unit_str})
            
        except Exception as e:
            logger.error(f"Create sketch on face error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
    
    def exit_sketch(self) -> Dict:
        """
        Exit the current sketch
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            
            doc.InsertSketch2(True)

            # Post-condition check: verify sketch mode exited
            still_active = False
            try:
                sk_mgr = getattr(doc, "SketchManager", None)
                if sk_mgr is not None:
                    act_sk = getattr(sk_mgr, "ActiveSketch", None)
                    if act_sk is not None and getattr(act_sk, "IsSketchEditable", False):
                        still_active = True
            except Exception:
                pass

            if still_active:
                return self._result(
                    False,
                    "Post-condition failed: Sketch mode is still active after exit_sketch",
                    SwErrors.swSketchError,
                )
            
            return self._result(True, "Exited sketch", SwErrors.swSuccess, {"sketch_active": False})
            
        except Exception as e:
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)

    def _require_active_sketch_in_3d(self, doc) -> Optional[Dict]:
        """In Part and Assembly documents, require an active sketch to avoid auto-creating orphan 3D sketches."""
        dtype = self.get_document_type(doc)
        if dtype in ("Part", "Assembly"):
            sk_mgr = getattr(doc, "SketchManager", None)
            if sk_mgr is None or getattr(sk_mgr, "ActiveSketch", None) is None:
                return self._result(False, "Failed - ensure sketch is active", SwErrors.swSketchError)
        return None
    
    def draw_line(self, x1: float = 0, y1: float = 0, 
                  x2: float = 100, y2: float = 0, unit: str = None,
                  construction: bool = False) -> Dict:
        """
        Draw a line in the active sketch
        
        Args:
            x1, y1: Start point coordinates
            x2, y2: End point coordinates
            unit: Unit for coordinates (uses default if None)
            construction: If True, set line as construction geometry (centerline)
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err
            
            # Reject zero-length line
            if abs(x1 - x2) < 1e-9 and abs(y1 - y2) < 1e-9:
                return self._result(
                    False,
                    f"Zero-length line rejected: start ({x1}, {y1}) and end ({x2}, {y2}) are identical.",
                    SwErrors.swInvalidParameter,
                )

            # Convert to meters
            x1_m = self._units.to_meters(x1, unit)
            y1_m = self._units.to_meters(y1, unit)
            x2_m = self._units.to_meters(x2, unit)
            y2_m = self._units.to_meters(y2, unit)
            
            # Draw line (no snapping: AddToDB)
            with direct_sketch(doc):
                line = doc.SketchManager.CreateLine(x1_m, y1_m, 0, x2_m, y2_m, 0)

            if line is None:
                return self._result(False, _NOT_CREATED.format(what="line"), SwErrors.swSketchError)

            got = line_points(line)
            if got is not None and not (close(got[0], (x1_m, y1_m)) and close(got[1], (x2_m, y2_m))):
                delete_segments(doc, [line])
                return self._result(
                    False,
                    f"Line was created at ({mm(got[0][0])}, {mm(got[0][1])})->({mm(got[1][0])}, {mm(got[1][1])}) mm "
                    "instead of the requested points; it was removed.",
                    SwErrors.swSketchError,
                )

            if construction:
                try:
                    line.ConstructionGeometry = True
                except Exception:
                    pass
            
            # Calculate length
            length_m = math.sqrt((x2_m - x1_m)**2 + (y2_m - y1_m)**2)
            length_display = self._units.from_meters(length_m, unit)
            unit_str = unit or self._units.default_unit.value
            
            data = {"length": length_display, "unit": unit_str, "verified": got is not None,
                    "construction": bool(construction)}
            frame = sketch_frame(doc)
            if frame:
                data["world_mm"] = [sketch_to_world_mm(frame, x1_m * 1000.0, y1_m * 1000.0),
                                    sketch_to_world_mm(frame, x2_m * 1000.0, y2_m * 1000.0)]
            return self._result(True, f"Line: {length_display:.2f}{unit_str}", SwErrors.swSuccess, data)
            
        except Exception as e:
            logger.error(f"Draw line error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
    
    def draw_circle(self, x: float = 0, y: float = 0, 
                    radius: float = 25, unit: str = None) -> Dict:
        """
        Draw a circle in the active sketch
        
        Args:
            x, y: Center point coordinates
            radius: Circle radius
            unit: Unit for dimensions (uses default if None)
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err
            
            if radius <= 0:
                return self._result(
                    False,
                    f"Invalid circle radius {radius}: radius must be greater than 0.",
                    SwErrors.swInvalidParameter,
                )

            # Convert to meters
            x_m = self._units.to_meters(x, unit)
            y_m = self._units.to_meters(y, unit)
            radius_m = self._units.to_meters(radius, unit)
            
            # Centre + radius (no edge point that could snap), no inference (AddToDB)
            with direct_sketch(doc):
                circle = doc.SketchManager.CreateCircleByRadius(x_m, y_m, 0, radius_m)

            if circle is None:
                return self._result(False, _NOT_CREATED.format(what="circle"), SwErrors.swSketchError)

            got = arc_center_radius(circle)
            if got is not None and not (close(got[0], (x_m, y_m)) and abs(got[1] - radius_m) <= TOL_M):
                delete_segments(doc, [circle])
                return self._result(
                    False,
                    f"Circle was created at ({mm(got[0][0])}, {mm(got[0][1])}) r={mm(got[1])} mm instead of "
                    f"({x}, {y}) r={radius}; it was removed.",
                    SwErrors.swSketchError,
                )

            unit_str = unit or self._units.default_unit.value
            data = {"radius": radius, "unit": unit_str, "center_x": x, "center_y": y,
                    "verified": got is not None}
            frame = sketch_frame(doc)
            if frame:
                data["center_world_mm"] = sketch_to_world_mm(
                    frame, self._units.from_meters(x_m, "mm"), self._units.from_meters(y_m, "mm"))
            return self._result(True, f"Circle: r={radius}{unit_str}", SwErrors.swSuccess, data)
            
        except Exception as e:
            logger.error(f"Draw circle error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
    
    def draw_rectangle(self, x1: float = -50, y1: float = -25,
                       x2: float = 50, y2: float = 25, unit: str = None) -> Dict:
        """
        Draw a rectangle in the active sketch
        
        Args:
            x1, y1: First corner coordinates
            x2, y2: Opposite corner coordinates
            unit: Unit for dimensions
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err
            
            if abs(x1 - x2) < 1e-9 or abs(y1 - y2) < 1e-9:
                return self._result(
                    False,
                    "Zero-area rectangle rejected: width and height must be greater than 0.",
                    SwErrors.swInvalidParameter,
                )

            # Convert to meters
            x1_m = self._units.to_meters(x1, unit)
            y1_m = self._units.to_meters(y1, unit)
            x2_m = self._units.to_meters(x2, unit)
            y2_m = self._units.to_meters(y2, unit)
            
            # Draw rectangle (no snapping: AddToDB)
            with direct_sketch(doc):
                rect = doc.SketchManager.CreateCornerRectangle(x1_m, y1_m, 0, x2_m, y2_m, 0)

            segs = list(rect) if rect is not None else []
            if not segs:
                return self._result(False, _NOT_CREATED.format(what="rectangle"), SwErrors.swSketchError)

            # Read back the four corners and compare with the requested box.
            want = (min(x1_m, x2_m), min(y1_m, y2_m), max(x1_m, x2_m), max(y1_m, y2_m))
            pts = [p for s in segs for p in (line_points(s) or ())]
            if pts:
                got = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
                if len(segs) != 4 or any(abs(g - w) > TOL_M for g, w in zip(got, want)):
                    delete_segments(doc, segs)
                    return self._result(
                        False,
                        f"Rectangle was created as x {mm(got[0])}..{mm(got[2])}, y {mm(got[1])}..{mm(got[3])} mm "
                        f"({len(segs)} segments) instead of the requested corners; it was removed.",
                        SwErrors.swSketchError,
                    )

            width = abs(x2 - x1)
            height = abs(y2 - y1)
            unit_str = unit or self._units.default_unit.value
            data = {"width": width, "height": height, "unit": unit_str, "verified": bool(pts)}
            frame = sketch_frame(doc)
            if frame:
                to_mm = lambda v: self._units.from_meters(v, "mm")  # noqa: E731
                c1 = sketch_to_world_mm(frame, to_mm(want[0]), to_mm(want[1]))
                c2 = sketch_to_world_mm(frame, to_mm(want[2]), to_mm(want[3]))
                data["world_bbox_mm"] = {"min": [min(a, b) for a, b in zip(c1, c2)],
                                         "max": [max(a, b) for a, b in zip(c1, c2)]}
                data["sketch_axes"] = axis_note(frame)
            return self._result(True, f"Rectangle: {width}x{height}{unit_str}", SwErrors.swSuccess, data)
            
        except Exception as e:
            logger.error(f"Draw rectangle error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
    
    def draw_arc_center(self, cx: float = 0, cy: float = 0, radius: float = 25,
                        start_angle: float = 0, end_angle: float = 90,
                        unit: str = None) -> Dict:
        """
        Draw an arc by center point and angles
        
        Args:
            cx, cy: Center point
            radius: Arc radius
            start_angle: Start angle in degrees
            end_angle: End angle in degrees
            unit: Unit for radius
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err
            
            if radius <= 0:
                return self._result(
                    False,
                    f"Invalid arc radius {radius}: radius must be greater than 0.",
                    SwErrors.swInvalidParameter,
                )

            # A sketch arc must span strictly between 0° and 360° (counter-clockwise from start).
            # 360 used to fail with "ensure sketch is active", and 450 drew 90° while reporting 450°.
            span = float(end_angle) - float(start_angle)
            if span <= 0 or span >= 360:
                return self._result(
                    False,
                    f"Invalid arc span {span}° (start={start_angle}, end={end_angle}): the counter-clockwise "
                    "sweep must be between 0 and 360 degrees. Use draw_circle for a full circle.",
                    SwErrors.swInvalidParameter,
                )

            # Convert to meters
            cx_m = self._units.to_meters(cx, unit)
            cy_m = self._units.to_meters(cy, unit)
            radius_m = self._units.to_meters(radius, unit)
            
            # Convert angles to radians
            start_rad = math.radians(start_angle)
            end_rad = math.radians(end_angle)
            
            # Calculate start and end points
            x1 = cx_m + radius_m * math.cos(start_rad)
            y1 = cy_m + radius_m * math.sin(start_rad)
            x2 = cx_m + radius_m * math.cos(end_rad)
            y2 = cy_m + radius_m * math.sin(end_rad)
            
            # Draw arc (center, start, end, direction), no snapping (AddToDB)
            with direct_sketch(doc):
                arc = doc.SketchManager.CreateArc(
                    cx_m, cy_m, 0,   # Center
                    x1, y1, 0,       # Start
                    x2, y2, 0,       # End
                    1                # Direction (1 = counter-clockwise)
                )

            if arc is None:
                return self._result(False, _NOT_CREATED.format(what="arc"), SwErrors.swSketchError)

            got = arc_center_radius(arc)
            if got is not None and not (close(got[0], (cx_m, cy_m)) and abs(got[1] - radius_m) <= TOL_M):
                delete_segments(doc, [arc])
                return self._result(
                    False,
                    f"Arc was created at ({mm(got[0][0])}, {mm(got[0][1])}) r={mm(got[1])} mm instead of the "
                    "requested centre/radius; it was removed.",
                    SwErrors.swSketchError,
                )
            
            unit_str = unit or self._units.default_unit.value
            arc_angle = abs(end_angle - start_angle)
            
            return self._result(True, f"Arc: r={radius}{unit_str}, {arc_angle}°",
                              SwErrors.swSuccess,
                              {"radius": radius, "start_angle": start_angle,
                               "end_angle": end_angle, "unit": unit_str})
            
        except Exception as e:
            logger.error(f"Draw arc error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
    
    def world_point_to_sketch(self, point, unit: str = None) -> Tuple[float, float]:
        """
        Convert a world/model point [x, y, z] (in `unit`) to the active sketch's local (x, y) in `unit`.
        Raises ValueError when there is no active sketch or the point is not on the sketch plane.
        """
        doc, err = self.get_active_doc()
        if err:
            raise ValueError(err.get("message", "No active document"))
        frame = sketch_frame(doc)
        if not frame:
            raise ValueError("World coordinates need an active sketch (call create_sketch first).")
        if not isinstance(point, (list, tuple)) or len(point) != 3:
            raise ValueError(f"A world point must be [x, y, z]; got {point!r}.")
        p_mm = [self._units.to_meters(float(c), unit) * 1000.0 for c in point]
        d = [p_mm[i] - frame["origin_mm"][i] for i in range(3)]
        dot = lambda v: sum(d[i] * v[i] for i in range(3))  # noqa: E731
        off = dot(frame["normal_world"])
        if abs(off) > 0.01:
            n = frame["normal_world"]
            plane_pos = sum(frame["origin_mm"][i] * n[i] for i in range(3))
            raise ValueError(
                f"World point {list(point)} is {abs(off):.4f} mm off the active sketch plane "
                f"(plane: normal {n}, at {plane_pos:.4f} mm along it; {axis_note(frame)}). "
                "Give a point that lies on the sketch plane.")
        return (self._units.from_meters(dot(frame["x_axis_world"]) / 1000.0, unit),
                self._units.from_meters(dot(frame["y_axis_world"]) / 1000.0, unit))

    def draw_arc_3pt(self, start, mid, end, unit: str = None) -> Dict:
        """Arc from `start` to `end` passing through `mid` (sketch-local [x, y] points)."""
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err
            (x1, y1), (x2, y2), (x3, y3) = [
                (self._units.to_meters(float(p[0]), unit), self._units.to_meters(float(p[1]), unit))
                for p in (start, mid, end)]
            det = 2.0 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
            if abs(det) < 1e-14:
                return self._result(False, "The three arc points are collinear or coincident.",
                                    SwErrors.swInvalidParameter)
            s1, s2, s3 = x1 * x1 + y1 * y1, x2 * x2 + y2 * y2, x3 * x3 + y3 * y3
            cx = (s1 * (y2 - y3) + s2 * (y3 - y1) + s3 * (y1 - y2)) / det
            cy = (s1 * (x3 - x2) + s2 * (x1 - x3) + s3 * (x2 - x1)) / det
            r = math.hypot(x1 - cx, y1 - cy)
            # Counter-clockwise from start to end when `mid` lies to the left of start->end is false.
            cross = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
            direction = 1 if cross > 0 else -1
            with direct_sketch(doc):
                arc = doc.SketchManager.CreateArc(cx, cy, 0, x1, y1, 0, x3, y3, 0, direction)
            if arc is None:
                return self._result(False, _NOT_CREATED.format(what="arc"), SwErrors.swSketchError)
            got = arc_center_radius(arc)
            ends = line_points(arc)
            wrong = (got is not None and not (close(got[0], (cx, cy), 1e-6) and abs(got[1] - r) <= 1e-6)) or (
                ends is not None and not (
                    (close(ends[0], (x1, y1), 1e-6) and close(ends[1], (x3, y3), 1e-6))
                    or (close(ends[0], (x3, y3), 1e-6) and close(ends[1], (x1, y1), 1e-6))))
            if wrong:
                delete_segments(doc, [arc])
                return self._result(False, "Arc was not created through the requested points; it was removed.",
                                    SwErrors.swSketchError)
            sweep = math.degrees((math.atan2(y3 - cy, x3 - cx) - math.atan2(y1 - cy, x1 - cx)) * direction) % 360.0
            unit_str = unit or self._units.default_unit.value
            data = {"radius": round(self._units.from_meters(r, unit), 6),
                    "center": [round(self._units.from_meters(cx, unit), 6), round(self._units.from_meters(cy, unit), 6)],
                    "sweep_deg": round(sweep, 4), "unit": unit_str, "verified": got is not None}
            frame = sketch_frame(doc)
            if frame:
                data["center_world_mm"] = sketch_to_world_mm(frame, cx * 1000.0, cy * 1000.0)
            return self._result(True, f"Arc through 3 points: r={data['radius']}{unit_str}, {data['sweep_deg']}°",
                                SwErrors.swSuccess, data)
        except Exception as e:
            logger.error(f"Draw 3-point arc error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)

    def draw_polygon(self, cx: float = 0, cy: float = 0, radius: float = 25,
                     sides: int = 6, unit: str = None, rotation: float = 0.0,
                     flats: bool = False) -> Dict:
        """
        Draw a regular polygon

        Args:
            cx, cy: Center point
            radius: Radius to the vertices, or to the flats (inscribed circle) when flats=True
            sides: Number of sides (3-100)
            rotation: Angle in degrees of the first vertex (flats=True: of the first flat's normal) from sketch +X
            unit: Unit for radius
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err
            
            if radius <= 0:
                return self._result(
                    False,
                    f"Invalid polygon radius {radius}: radius must be greater than 0.",
                    SwErrors.swInvalidParameter,
                )

            if sides < 3 or sides > 100:
                return self._result(False, "Sides must be between 3 and 100",
                                  SwErrors.swInvalidInput)
            
            # Convert to meters
            cx_m = self._units.to_meters(cx, unit)
            cy_m = self._units.to_meters(cy, unit)
            radius_m = self._units.to_meters(radius, unit)
            
            # CreatePolygon returns nothing while AddToDB is on (and snaps when it is off), so the
            # polygon is built from exact line segments instead.
            sides = int(sides)
            r_vertex = radius_m / math.cos(math.pi / sides) if flats else radius_m
            a0 = math.radians(float(rotation or 0.0)) + (math.pi / sides if flats else 0.0)
            verts = [(cx_m + r_vertex * math.cos(a0 + 2 * math.pi * i / sides),
                      cy_m + r_vertex * math.sin(a0 + 2 * math.pi * i / sides)) for i in range(sides)]
            created, bad = [], 0
            with direct_sketch(doc):
                for i in range(sides):
                    (ax, ay), (bx, by) = verts[i], verts[(i + 1) % sides]
                    seg = doc.SketchManager.CreateLine(ax, ay, 0.0, bx, by, 0.0)
                    if seg is None:
                        bad += 1
                        continue
                    created.append(seg)
                    got = line_points(seg)
                    if got is not None and not (close(got[0], (ax, ay)) and close(got[1], (bx, by))):
                        bad += 1

            if bad or len(created) != sides:
                delete_segments(doc, created)
                return self._result(
                    False,
                    f"Polygon not created: {len(created) - bad} of {sides} sides were made correctly. "
                    "Nothing was left in the sketch.",
                    SwErrors.swSketchError)

            unit_str = unit or self._units.default_unit.value
            data = {"sides": sides, "radius": radius, "radius_to": "flats" if flats else "vertices",
                    "vertex_radius": round(self._units.from_meters(r_vertex, unit), 6),
                    "across_flats": round(self._units.from_meters(2 * r_vertex * math.cos(math.pi / sides), unit), 6),
                    "rotation_deg": float(rotation or 0.0), "unit": unit_str, "verified": True}
            frame = sketch_frame(doc)
            if frame:
                data["center_world_mm"] = sketch_to_world_mm(
                    frame, self._units.from_meters(cx_m, "mm"), self._units.from_meters(cy_m, "mm"))
            return self._result(True, f"{sides}-sided polygon: r={radius}{unit_str} to the "
                                      f"{'flats' if flats else 'vertices'}",
                                SwErrors.swSuccess, data)
            
        except Exception as e:
            logger.error(f"Draw polygon error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
    
    def draw_centerline(self, x1: float = 0, y1: float = -50,
                        x2: float = 0, y2: float = 50, unit: str = None) -> Dict:
        """
        Draw a centerline (for revolve axis)
        
        Args:
            x1, y1: Start point
            x2, y2: End point
            unit: Unit for coordinates
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err
            
            # Convert to meters
            x1_m = self._units.to_meters(x1, unit)
            y1_m = self._units.to_meters(y1, unit)
            x2_m = self._units.to_meters(x2, unit)
            y2_m = self._units.to_meters(y2, unit)
            
            # Draw centerline, no snapping (AddToDB)
            with direct_sketch(doc):
                line = doc.SketchManager.CreateCenterLine(x1_m, y1_m, 0, x2_m, y2_m, 0)

            if line is None:
                return self._result(False, _NOT_CREATED.format(what="centerline"), SwErrors.swSketchError)
            
            return self._result(True, "Centerline created",
                              SwErrors.swSuccess)
            
        except Exception as e:
            logger.error(f"Draw centerline error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)

    def draw_text(self, text: str, x: float = 0.0, y: float = 0.0,
                  height: float = 12.0, font_name: str = "Segoe UI",
                  bold: bool = True, italic: bool = False,
                  angle: float = 0.0, center: bool = False,
                  unit: str = None) -> Dict:
        """
        Draw sketch text in the active sketch
        
        Args:
            text: String text to insert (supports \n for multi-line)
            x, y: Placement coordinates (origin of text)
            height: Font character height in units (default mm)
            font_name: Font typeface name (default 'Segoe UI')
            bold: Bold formatting
            italic: Italic formatting
            angle: Text rotation angle in degrees (e.g. 0, 90, 180, 270)
            center: Center-align text horizontally
            unit: Unit for coordinates and height (default active unit)
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err

            if not text:
                return self._result(
                    False,
                    "Text parameter cannot be empty.",
                    SwErrors.swInvalidParameter,
                )

            # Convert coordinates and height to meters
            x_m = self._units.to_meters(x, unit)
            y_m = self._units.to_meters(y, unit)
            height_m = self._units.to_meters(height, unit)

            formatted_text = f"<r{int(angle)}>{text}" if angle != 0.0 else text
            alignment = 1 if center else 0

            # Insert sketch text via ModelDoc2::InsertSketchText
            sketch_text = doc.InsertSketchText(x_m, y_m, 0.0, formatted_text, alignment, 0, 0, 100, 100)
            if sketch_text is None:
                return self._result(
                    False,
                    "Failed to insert sketch text - ensure sketch is active",
                    SwErrors.swSketchError,
                )

            # Customize font format if supported
            try:
                tf = None
                ole = getattr(sketch_text, "_oleobj_", None)
                if ole is not None:
                    # ISketchText::GetTextFormat is DISPID 8 in SolidWorks COM API
                    try:
                        tf_disp = ole.Invoke(8, 0, pythoncom.DISPATCH_METHOD, True)
                        tf = win32com.client.Dispatch(tf_disp)
                    except Exception:
                        get_tf = getattr(sketch_text, "GetTextFormat", None)
                        tf = get_tf() if callable(get_tf) else get_tf
                else:
                    get_tf = getattr(sketch_text, "GetTextFormat", None)
                    tf = get_tf() if callable(get_tf) else get_tf

                if tf:
                    if font_name:
                        tf.TypeFaceName = font_name
                    if height_m > 0:
                        tf.CharHeight = height_m
                    tf.Bold = bold
                    tf.Italic = italic
                    if ole is not None:
                        # ISketchText::SetTextFormat is DISPID 10: SetTextFormat(UseDoc: False, TextFormat: IDispatch)
                        try:
                            tf_ole = getattr(tf, "_oleobj_", tf)
                            ole.Invoke(10, 0, pythoncom.DISPATCH_METHOD, True, False, tf_ole)
                        except Exception:
                            set_tf = getattr(sketch_text, "SetTextFormat", None)
                            if callable(set_tf):
                                set_tf(False, tf)
                    else:
                        set_tf = getattr(sketch_text, "SetTextFormat", None)
                        if callable(set_tf):
                            set_tf(False, tf)
            except Exception as fe:
                logger.debug(f"Could not apply font formatting: {fe}")

            # The justification argument of InsertSketchText has no effect on free sketch text: the
            # text always starts at (x, y) (MCP-049). Measure the glyph outlines (model space),
            # convert them to sketch x/y and, for center=True, shift the text so its extent is
            # centred on x.
            from ..core.com_compat import get_attr_or_call
            frame = sketch_frame(doc)

            def _text_extent_mm():
                if not frame:
                    return None
                o, ex, ey = frame["origin_mm"], frame["x_axis_world"], frame["y_axis_world"]
                xs, ys = [], []
                for edge in (get_attr_or_call(sketch_text, "GetEdges") or []):
                    try:
                        p = [float(v) * 1000.0 for v in get_attr_or_call(edge, "GetCurveParams2")]
                    except Exception:
                        continue
                    for q in (p[0:3], p[3:6]):
                        d = [q[i] - o[i] for i in range(3)]
                        xs.append(sum(d[i] * ex[i] for i in range(3)))
                        ys.append(sum(d[i] * ey[i] for i in range(3)))
                if not xs:
                    return None
                return [min(xs), max(xs), min(ys), max(ys)]

            extent = None
            try:
                try:
                    doc.GraphicsRedraw2()
                except Exception:
                    pass
                extent = _text_extent_mm()
                if center and extent is not None:
                    x_target_mm = x_m * 1000.0
                    dx_mm = x_target_mm - (extent[0] + extent[1]) / 2.0
                    if abs(dx_mm) > 1e-4:
                        sketch_text.SetCoordinates(x_m + dx_mm / 1000.0, y_m, 0.0)
                        try:
                            doc.GraphicsRedraw2()
                        except Exception:
                            pass
                        extent = _text_extent_mm()
            except Exception as ce:
                logger.debug(f"draw_text extent/centring failed: {ce}")

            if center and extent is None:
                return self._result(
                    False,
                    "Text inserted, but its extent could not be measured, so it could not be centred "
                    "(it starts at x). Delete it or place it with an explicit left x.",
                    SwErrors.swSketchError,
                )

            unit_str = unit or self._units.default_unit.value
            data = {"text": text, "x": x, "y": y, "height": height, "font": font_name, "angle": angle, "center": center, "unit": unit_str}
            msg = f"Inserted sketch text: '{text[:30]}' at ({x}, {y})"
            if extent is not None:
                ext_u = [round(self._units.from_meters(v / 1000.0, unit), 3) for v in extent]
                data["extent_sketch"] = {"x_min": ext_u[0], "x_max": ext_u[1], "y_min": ext_u[2], "y_max": ext_u[3]}
                msg += (f"; glyphs span sketch x {ext_u[0]}..{ext_u[1]}, y {ext_u[2]}..{ext_u[3]} {unit_str}"
                        + (" (centred on x)" if center else " (left-aligned at x)"))
            return self._result(True, msg, SwErrors.swSuccess, data)
        except Exception as e:
            logger.error(f"Draw text error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)

    def draw_closed_profile(
        self,
        points: List[List[float]],
        unit: Optional[str] = None,
    ) -> Dict:
        """
        Draw a closed polygon/profile loop from a list of [x, y] points in a single call.
        Matches lminventor's draw_closed_profile tool.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err

            if not points or len(points) < 3:
                return self._result(
                    False,
                    "Need at least 3 [x, y] points for a closed profile.",
                    SwErrors.swInvalidParameter,
                )

            unit_str = unit or self._units.default_unit.value
            pts_m = [
                (self._units.to_meters(float(pt[0]), unit), self._units.to_meters(float(pt[1]), unit))
                for pt in points
            ]

            # One segment per consecutive pair (closing back to the first point).
            n = len(pts_m)
            pairs = [(pts_m[i], pts_m[(i + 1) % n]) for i in range(n)
                     if not (abs(pts_m[i][0] - pts_m[(i + 1) % n][0]) < 1e-9 and abs(pts_m[i][1] - pts_m[(i + 1) % n][1]) < 1e-9)]
            created = []
            bad = []
            with direct_sketch(doc):  # no snapping: corners used to merge and leave open profiles
                for (ax, ay), (bx, by) in pairs:
                    seg = doc.SketchManager.CreateLine(ax, ay, 0.0, bx, by, 0.0)
                    if seg is None:
                        bad.append(((ax, ay), (bx, by), None))
                        continue
                    created.append(seg)
                    got = line_points(seg)
                    if got is not None and not (close(got[0], (ax, ay)) and close(got[1], (bx, by))):
                        bad.append(((ax, ay), (bx, by), got))
            created_lines = len(created)

            if bad or created_lines != len(pairs) or created_lines < 3:
                delete_segments(doc, created)
                detail = "; ".join(
                    f"({mm(a[0])}, {mm(a[1])})->({mm(b[0])}, {mm(b[1])}) " + ("not created" if g is None else
                    f"created as ({mm(g[0][0])}, {mm(g[0][1])})->({mm(g[1][0])}, {mm(g[1][1])})")
                    for a, b, g in bad[:4]
                )
                return self._result(
                    False,
                    f"Closed profile not created: {created_lines} of {len(pairs)} segments were made correctly"
                    + (f" ({detail})" if detail else "") + ". Nothing was left in the sketch.",
                    SwErrors.swSketchError,
                )

            data = {"point_count": len(points), "line_count": created_lines, "unit": unit_str, "verified": True}
            frame = sketch_frame(doc)
            if frame:
                wpts = [sketch_to_world_mm(frame, x * 1000.0, y * 1000.0) for x, y in pts_m]
                data["world_bbox_mm"] = {"min": [min(p[i] for p in wpts) for i in range(3)],
                                         "max": [max(p[i] for p in wpts) for i in range(3)]}
                data["sketch_axes"] = axis_note(frame)
            return self._result(
                True,
                f"Closed profile with {len(points)} points ({created_lines} lines, unit={unit_str})",
                SwErrors.swSuccess,
                data,
            )
        except Exception as e:
            logger.error(f"draw_closed_profile error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
