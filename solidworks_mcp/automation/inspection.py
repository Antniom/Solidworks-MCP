"""
SolidWorks Inspection, Verification, and Viewport Capture
---------------------------------------------------------
Provides model measurement, mass property inspection, automated geometric
verification against requested tolerances, and viewport image capture.
"""

import os
import time
import logging
import traceback
from typing import Any, Dict, List, Optional, Tuple

import math
import win32com.client
import pythoncom

from ..constants import SwErrors, SwViews
from ..core.com_compat import get_attr_or_call
from ..core import (
    ModelVerifier,
    VerificationReport,
    DeclarativeSelectionResolver,
    SelectionTarget,
    SelectionResult,
    get_fs_policy,
)

logger = logging.getLogger(__name__)


class InspectionOperations:
    """
    Mixin class for geometry inspection, verification, and viewport capture.
    """

    def inspect_model(self) -> Dict[str, Any]:
        """
        Inspect the active model: bodies, bounding box, volume, mass properties, and rebuild state.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            dtype = self.get_document_type(doc)
            if dtype == "Drawing":
                return self._result(
                    False,
                    "inspect_model is not supported for Drawing documents (requires Part or Assembly)",
                    SwErrors.swInvalidDocumentType
                )

            report = ModelVerifier.verify(doc)
            res_dict = report.to_dict()
            res_dict["volume"] = res_dict.get("volume_mm3", 0.0)

            # Query face and edge counts from bodies
            faces_count = 0
            edges_count = 0
            try:
                bodies = doc.GetBodies2(0, False)
                if bodies:
                    for b in bodies:
                        try:
                            fc = getattr(b, "GetFaceCount", None)
                            faces_count += int(fc() if callable(fc) else fc)
                        except Exception:
                            pass
                        try:
                            ec = getattr(b, "GetEdgeCount", None)
                            edges_count += int(ec() if callable(ec) else ec)
                        except Exception:
                            pass
            except Exception:
                pass
            res_dict["faces_count"] = faces_count
            res_dict["edges_count"] = edges_count

            return self._result(
                True,
                f"Inspection complete: {report.body_count} bodies, bounding box: {report.dimensions_mm['dx']}x{report.dimensions_mm['dy']}x{report.dimensions_mm['dz']} mm, volume: {res_dict['volume']:.2f} mm3",
                SwErrors.swSuccess,
                res_dict,
            )
        except Exception as e:
            logger.error(f"Inspect model error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Inspection error: {e}", SwErrors.swGeneralError)

    def verify_model(
        self,
        expected_dx: Optional[float] = None,
        expected_dy: Optional[float] = None,
        expected_dz: Optional[float] = None,
        expected_bodies: Optional[int] = None,
        tolerance_mm: float = 0.2,
    ) -> Dict[str, Any]:
        """
        Verify active model geometry against expected dimensions and tolerance.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            expected_dims = {}
            if expected_dx is not None:
                expected_dims["dx"] = float(expected_dx)
            if expected_dy is not None:
                expected_dims["dy"] = float(expected_dy)
            if expected_dz is not None:
                expected_dims["dz"] = float(expected_dz)

            report = ModelVerifier.verify(
                doc,
                expected_dimensions=expected_dims if expected_dims else None,
                expected_bodies=expected_bodies,
                tolerance_mm=tolerance_mm,
            )

            status_msg = "PASSED" if report.passed else "FAILED"
            return self._result(
                report.passed,
                f"Model verification {status_msg}: {len(report.warnings)} warnings",
                SwErrors.swSuccess if report.passed else SwErrors.swFeatureError,
                report.to_dict(),
            )
        except Exception as e:
            logger.error(f"Verify model error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Verification error: {e}", SwErrors.swGeneralError)

    def capture_viewport(
        self,
        filepath: Optional[str] = None,
        view_name: str = "*Isometric",
    ) -> Dict[str, Any]:
        """
        Capture the current 3D viewport to a PNG or JPG image.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            # Default filepath
            if not filepath:
                screenshots_dir = os.path.join(os.getcwd(), "screenshots")
                os.makedirs(screenshots_dir, exist_ok=True)
                filename = f"capture_{int(time.time()*1000)}.png"
                filepath = os.path.join(screenshots_dir, filename)

            # Security check
            policy = get_fs_policy()
            is_valid, canonical, err_msg = policy.validate_path(
                filepath,
                must_exist=False,
                allowed_extensions={".png", ".jpg", ".jpeg", ".bmp"},
            )
            if not is_valid:
                return self._result(
                    False,
                    f"Filesystem security violation: {err_msg}",
                    SwErrors.swFileSaveError,
                )
            filepath = canonical

            # Set orientation and zoom to fit
            try:
                # swStandardViews_e. Unknown/custom named views use -1 so the name is honoured.
                standard_views = {
                    "front": 1, "back": 2, "left": 3, "right": 4, "top": 5, "bottom": 6,
                    "isometric": 7, "trimetric": 8, "dimetric": 9,
                }
                view_id = standard_views.get(view_name.strip().lstrip("*").lower(), -1)

                doc.ShowNamedView2(view_name, view_id)
                doc.ViewZoomtofit2()
                doc.GraphicsRedraw2()
            except Exception as e:
                logger.debug(f"View adjustment warning: {e}")

            # Save image
            saved = False
            try:
                # SaveAs3(filepath, 0=swSaveAsCurrentVersion, 2=swSaveAsOptions_Silent)
                res = doc.SaveAs3(filepath, 0, 2)
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    saved = True
            except Exception as e:
                logger.debug(f"doc.SaveAs3 image failed: {e}")

            if not saved:
                try:
                    doc.SaveAs(filepath)
                    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                        saved = True
                except Exception as e:
                    logger.debug(f"doc.SaveAs image failed: {e}")

            if not saved:
                try:
                    empty_export = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
                    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                    doc.Extension.SaveAs(filepath, 0, 0, empty_export, errors, warnings)
                    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                        saved = True
                except Exception as e:
                    logger.debug(f"Extension.SaveAs image failed: {e}")

            if not saved:
                try:
                    tmp_bmp = os.path.splitext(filepath)[0] + "_temp.bmp"
                    doc.SaveBMP(tmp_bmp, 0, 0)
                    if os.path.exists(tmp_bmp) and os.path.getsize(tmp_bmp) > 0:
                        try:
                            from PIL import Image
                            with Image.open(tmp_bmp) as im:
                                im.save(filepath)
                            saved = True
                        except Exception as pil_err:
                            logger.debug(f"PIL conversion failed: {pil_err}")
                        try:
                            os.remove(tmp_bmp)
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug(f"SaveBMP fallback failed: {e}")

            if not saved or not os.path.exists(filepath):
                return self._result(
                    False,
                    f"Failed to capture viewport to image '{filepath}'",
                    SwErrors.swExportError,
                )

            size_bytes = os.path.getsize(filepath)
            return self._result(
                True,
                f"Viewport captured: {filepath} ({size_bytes:,} bytes)",
                SwErrors.swSuccess,
                {
                    "filepath": filepath,
                    "size_bytes": size_bytes,
                    "view_name": view_name,
                },
            )
        except Exception as e:
            logger.error(f"Capture viewport error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Capture viewport error: {e}", SwErrors.swExportError)

    def select_entity(
        self,
        target_type: str = "face",
        name: Optional[str] = None,
        point: Optional[List[float]] = None,
        unit: str = "mm",
        direction: Optional[str] = None,
        mark: int = 0,
        append: bool = False,
    ) -> Dict[str, Any]:
        """
        Declaratively select a plane, feature, face, edge, or vertex.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            target = SelectionTarget(
                target_type=target_type,
                name=name,
                point=point,
                unit=unit,
                direction=direction,
                mark=mark,
                append=append,
            )

            sel_mgr = getattr(doc, "SelectionManager", None)
            pre_sel_count = 0
            if sel_mgr:
                try:
                    pre_sel_count = sel_mgr.GetSelectedObjectCount2(-1)
                except Exception:
                    try:
                        pre_sel_count = sel_mgr.GetSelectedObjectCount()
                    except Exception:
                        pass

            res = DeclarativeSelectionResolver.resolve_and_select(doc, target, self._units)

            post_sel_count = 0
            if sel_mgr:
                try:
                    post_sel_count = sel_mgr.GetSelectedObjectCount2(-1)
                except Exception:
                    try:
                        post_sel_count = sel_mgr.GetSelectedObjectCount()
                    except Exception:
                        pass

            # Post-condition check: verify selection count
            if res.success:
                if append and post_sel_count <= pre_sel_count:
                    res.success = False
                    res.error = f"Post-condition failed: Appended selection did not increase count (before: {pre_sel_count}, after: {post_sel_count})"
                elif not append and post_sel_count < 1:
                    res.success = False
                    res.error = f"Post-condition failed: Active selection count is 0 after select_entity"

            res_dict = res.to_dict()
            res_dict["selection_count"] = post_sel_count

            return self._result(
                res.success,
                res.message if res.success else (res.error or "Selection failed"),
                SwErrors.swSuccess if res.success else SwErrors.swSelectionError,
                res_dict,
            )
        except Exception as e:
            logger.error(f"Select entity error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Selection error: {e}", SwErrors.swSelectionError)

    def select_by_geometry(
        self,
        target_type: str = "face",
        surface_type: Optional[str] = None,
        normal: Optional[List[float]] = None,
        normal_tolerance: float = 0.05,
        radius: Optional[float] = None,
        radius_tolerance: float = 0.05,
        point_on_surface: Optional[List[float]] = None,
        curve_type: Optional[str] = None,
        length: Optional[float] = None,
        length_tolerance: float = 0.1,
        midpoint: Optional[List[float]] = None,
        point: Optional[List[float]] = None,
        bbox_min: Optional[List[float]] = None,
        bbox_max: Optional[List[float]] = None,
        mark: int = 0,
        append: bool = False,
        unit: str = "mm",
    ) -> Dict[str, Any]:
        """
        Select model entities (faces, edges, vertices) by geometric filters.

        Args:
            target_type: 'face', 'edge', or 'vertex'
            surface_type: For face: 'plane', 'cylinder', 'cone', 'sphere', 'torus', 'bspline'
            normal: For face: [nx, ny, nz] direction vector
            normal_tolerance: Angular/vector tolerance for normal matching (default 0.05)
            radius: Radius for cylindrical/spherical/toroidal face or circular edge (in user units)
            radius_tolerance: Radius tolerance (in user units, default 0.05)
            point_on_surface: [x, y, z] point that must lie on the target face
            curve_type: For edge: 'line', 'circle', 'arc', 'bspline'
            length: Edge length (in user units)
            length_tolerance: Edge length tolerance (in user units, default 0.1)
            midpoint: [x, y, z] target midpoint of the edge
            point: For vertex: [x, y, z] target vertex coordinates
            bbox_min: [xmin, ymin, zmin] lower bounding box filter
            bbox_max: [xmax, ymax, zmax] upper bounding box filter
            mark: Selection mark (default 0)
            append: If True, add to current selection; if False, clear selection first
            unit: Unit for coordinate/length/radius parameters (default 'mm')
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            dtype = self.get_document_type(doc)
            if dtype == "Drawing":
                return self._result(
                    False,
                    "select_by_geometry is not supported for Drawing documents (requires Part or Assembly)",
                    SwErrors.swInvalidDocumentType,
                )

            target_type_clean = (target_type or "face").strip().lower()
            if target_type_clean not in ("face", "edge", "vertex"):
                return self._result(
                    False,
                    f"Unsupported target_type '{target_type}'. Use 'face', 'edge', or 'vertex'.",
                    SwErrors.swInvalidInput,
                )

            # Scale factors to meters (SolidWorks internal unit)
            scale = self._units.to_meters(1.0, unit)
            radius_m = radius * scale if radius is not None else None
            radius_tol_m = radius_tolerance * scale if radius_tolerance is not None else 0.0001
            length_m = length * scale if length is not None else None
            length_tol_m = length_tolerance * scale if length_tolerance is not None else 0.0001
            point_m = [c * scale for c in point] if point else None
            pos_m = [c * scale for c in point_on_surface] if point_on_surface else None
            mid_m = [c * scale for c in midpoint] if midpoint else None
            bmin_m = [c * scale for c in bbox_min] if bbox_min else None
            bmax_m = [c * scale for c in bbox_max] if bbox_max else None

            req_norm = None
            if normal and len(normal) >= 3:
                n_len = math.sqrt(sum(c * c for c in normal[:3]))
                if n_len > 1e-9:
                    req_norm = [c / n_len for c in normal[:3]]

            bodies = doc.GetBodies2(0, False) if callable(getattr(doc, "GetBodies2", None)) else getattr(doc, "GetBodies2", None)
            if not bodies:
                return self._result(False, "No solid bodies found in active model.", SwErrors.swSelectionError)

            matched_entities = []
            entity_descriptors = []

            for body in bodies:
                if target_type_clean == "face":
                    faces = get_attr_or_call(body, "GetFaces") or []
                    for f_idx, f in enumerate(faces):
                        surf = get_attr_or_call(f, "GetSurface")
                        if not surf:
                            continue

                        # Surface type check via Identity() (swSurfaceTypes_e)
                        # PLANE_TYPE 4001, CYLINDER_TYPE 4002, CONE_TYPE 4003, SPHERE_TYPE 4004, TORUS_TYPE 4005, BSURF_TYPE 4006
                        stype_id = None
                        try:
                            stype_id = get_attr_or_call(surf, "Identity")
                        except Exception:
                            pass

                        surface_map = {
                            4001: "plane",
                            4002: "cylinder",
                            4003: "cone",
                            4004: "sphere",
                            4005: "torus",
                            4006: "bspline",
                            4007: "blend",
                            4008: "offset",
                            4009: "extrude",
                            4010: "revolution",
                        }
                        stype_str = surface_map.get(stype_id, "other")
                        is_plane = (stype_id == 4001)
                        is_cyl = (stype_id == 4002)
                        is_cone = (stype_id == 4003)
                        is_sphere = (stype_id == 4004)
                        is_torus = (stype_id == 4005)

                        if surface_type and surface_type.strip().lower() != stype_str:
                            continue

                        # Normal check (for planar or general surfaces)
                        f_normal = None
                        try:
                            fn = getattr(f, "Normal", None)
                            if callable(fn):
                                fn = fn()
                            if fn and len(fn) >= 3:
                                f_normal = [fn[0], fn[1], fn[2]]
                        except Exception:
                            pass

                        if not f_normal and is_plane:
                            try:
                                params = get_attr_or_call(surf, "PlaneParams")
                                if params and len(params) >= 3:
                                    f_normal = [params[0], params[1], params[2]]
                            except Exception:
                                pass

                        if req_norm:
                            if not f_normal:
                                continue
                            dot = sum(a * b for a, b in zip(f_normal, req_norm))
                            if abs(1.0 - dot) > normal_tolerance:
                                continue

                        # Radius check
                        f_radius = None
                        if is_cyl:
                            cparams = get_attr_or_call(surf, "CylinderParams")
                            if cparams and len(cparams) >= 7:
                                f_radius = cparams[6]
                        elif is_sphere:
                            sparams = get_attr_or_call(surf, "SphereParams")
                            if sparams and len(sparams) >= 4:
                                f_radius = sparams[3]

                        if radius_m is not None:
                            if f_radius is None or abs(f_radius - radius_m) > radius_tol_m:
                                continue

                        # Bounding box check
                        box = get_attr_or_call(f, "GetBox")
                        if box and len(box) >= 6:
                            if bmin_m and any(box[i] < bmin_m[i] - 1e-4 for i in range(3)):
                                continue
                            if bmax_m and any(box[i + 3] > bmax_m[i] + 1e-4 for i in range(3)):
                                continue
                            if pos_m:
                                # Check point inside face box
                                if not (box[0] - 1e-3 <= pos_m[0] <= box[3] + 1e-3 and
                                        box[1] - 1e-3 <= pos_m[1] <= box[4] + 1e-3 and
                                        box[2] - 1e-3 <= pos_m[2] <= box[5] + 1e-3):
                                    continue

                        area_m2 = get_attr_or_call(f, "GetArea") or 0.0
                        centroid_m = [(box[i] + box[i + 3]) / 2.0 for i in range(3)] if box and len(box) >= 6 else None

                        desc = {
                            "index": f_idx,
                            "type": "face",
                            "surface_type": stype_str,
                            "area_mm2": round(area_m2 * 1e6, 2),
                            "centroid_mm": [round(c * 1000, 2) for c in centroid_m] if centroid_m else None,
                            "normal": [round(c, 4) for c in f_normal] if f_normal else None,
                            "radius_mm": round(f_radius * 1000, 2) if f_radius is not None else None,
                        }
                        matched_entities.append(f)
                        entity_descriptors.append(desc)

                elif target_type_clean == "edge":
                    edges = get_attr_or_call(body, "GetEdges") or []
                    for e_idx, e in enumerate(edges):
                        curve = get_attr_or_call(e, "GetCurve")
                        if not curve:
                            continue

                        # Curve type check via Identity() (swCurveTypes_e)
                        # LINE_TYPE 3001, CIRCLE_TYPE 3002, ELLIPSE_TYPE 3003, BCURVE_TYPE 3005
                        ctype_id = None
                        try:
                            ctype_id = get_attr_or_call(curve, "Identity")
                        except Exception:
                            pass

                        curve_map = {
                            3001: "line",
                            3002: "circle",
                            3003: "ellipse",
                            3004: "intersection",
                            3005: "bspline",
                            3006: "spcurve",
                        }
                        ctype_str = curve_map.get(ctype_id, "other")
                        is_line = (ctype_id == 3001)
                        is_circle = (ctype_id == 3002)

                        if curve_type and curve_type.strip().lower() != ctype_str:
                            continue

                        # Vertices and length
                        v1 = get_attr_or_call(e, "GetStartVertex")
                        v2 = get_attr_or_call(e, "GetEndVertex")
                        pt1 = get_attr_or_call(v1, "GetPoint") if v1 else None
                        pt2 = get_attr_or_call(v2, "GetPoint") if v2 else None

                        e_len_m = 0.0
                        e_mid_m = None
                        if pt1 and pt2:
                            e_len_m = math.sqrt(sum((a - b) ** 2 for a, b in zip(pt1, pt2)))
                            e_mid_m = [(a + b) / 2.0 for a, b in zip(pt1, pt2)]
                        else:
                            # Closed circular edge
                            cparams = get_attr_or_call(curve, "CircleParams")
                            if cparams and len(cparams) >= 7:
                                e_len_m = 2.0 * math.pi * cparams[6]
                                e_mid_m = [cparams[0], cparams[1], cparams[2]]

                        # Length filter
                        if length_m is not None:
                            if abs(e_len_m - length_m) > length_tol_m:
                                continue

                        # Radius filter
                        e_radius = None
                        if is_circle:
                            cparams = get_attr_or_call(curve, "CircleParams")
                            if cparams and len(cparams) >= 7:
                                e_radius = cparams[6]

                        if radius_m is not None:
                            if e_radius is None or abs(e_radius - radius_m) > radius_tol_m:
                                continue

                        # Midpoint filter
                        if mid_m:
                            if not e_mid_m:
                                continue
                            dist_to_mid = math.sqrt(sum((a - b) ** 2 for a, b in zip(e_mid_m, mid_m)))
                            if dist_to_mid > 0.001:  # 1 mm tolerance
                                continue

                        # Bounding box filter
                        box = None
                        if pt1 and pt2:
                            box = [min(pt1[i], pt2[i]) for i in range(3)] + [max(pt1[i], pt2[i]) for i in range(3)]
                        elif e_radius is not None and e_mid_m:
                            box = [e_mid_m[i] - e_radius for i in range(3)] + [e_mid_m[i] + e_radius for i in range(3)]

                        if box:
                            if bmin_m and any(box[i] < bmin_m[i] - 1e-4 for i in range(3)):
                                continue
                            if bmax_m and any(box[i + 3] > bmax_m[i] + 1e-4 for i in range(3)):
                                continue

                        desc = {
                            "index": e_idx,
                            "type": "edge",
                            "curve_type": ctype_str,
                            "length_mm": round(e_len_m * 1000, 2),
                            "midpoint_mm": [round(c * 1000, 2) for c in e_mid_m] if e_mid_m else None,
                            "radius_mm": round(e_radius * 1000, 2) if e_radius is not None else None,
                        }
                        matched_entities.append(e)
                        entity_descriptors.append(desc)

                elif target_type_clean == "vertex":
                    vertices = get_attr_or_call(body, "GetVertices") or []
                    for v_idx, v in enumerate(vertices):
                        pt = get_attr_or_call(v, "GetPoint")
                        if not pt:
                            continue

                        if point_m:
                            dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(pt, point_m)))
                            if dist > 0.001:
                                continue

                        if bmin_m and any(pt[i] < bmin_m[i] - 1e-4 for i in range(3)):
                            continue
                        if bmax_m and any(pt[i] > bmax_m[i] + 1e-4 for i in range(3)):
                            continue

                        desc = {
                            "index": v_idx,
                            "type": "vertex",
                            "point_mm": [round(c * 1000, 2) for c in pt],
                        }
                        matched_entities.append(v)
                        entity_descriptors.append(desc)

            if not matched_entities:
                return self._result(
                    False,
                    f"No {target_type_clean}s matched the specified geometric filters.",
                    SwErrors.swSelectionError,
                    {"selected_count": 0, "entities": []},
                )

            # Apply selection in SolidWorks
            sel_mgr = getattr(doc, "SelectionManager", None)
            pre_count = 0
            if sel_mgr:
                try:
                    pre_count = sel_mgr.GetSelectedObjectCount2(-1)
                except Exception:
                    pass

            if not append:
                try:
                    doc.ClearSelection2(True)
                except Exception:
                    pass

            for entity in matched_entities:
                try:
                    entity.Select2(True, mark)
                except Exception as e_sel:
                    logger.debug(f"entity.Select2 failed: {e_sel}")

            post_count = 0
            if sel_mgr:
                try:
                    post_count = sel_mgr.GetSelectedObjectCount2(-1)
                except Exception:
                    pass

            # Post-condition check: verify selection count
            if append and post_count <= pre_count:
                return self._result(
                    False,
                    f"Post-condition failed: Selection count did not increase (before: {pre_count}, after: {post_count})",
                    SwErrors.swSelectionError,
                )
            elif not append and post_count < len(matched_entities):
                return self._result(
                    False,
                    f"Post-condition failed: Selected count ({post_count}) is less than matched entities ({len(matched_entities)})",
                    SwErrors.swSelectionError,
                )

            return self._result(
                True,
                f"Selected {len(matched_entities)} {target_type_clean}(s) by geometry filters.",
                SwErrors.swSuccess,
                {
                    "selected_count": len(matched_entities),
                    "total_selection_count": post_count,
                    "target_type": target_type_clean,
                    "entities": entity_descriptors,
                },
            )
        except Exception as e:
            logger.error(f"Select by geometry error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"select_by_geometry error: {e}", SwErrors.swGeneralError)

    # ========================================================================
    # Compact Token-Saving Topology Helpers & Showcase Rendering (lminventor)
    # ========================================================================

    def list_faces(
        self,
        body_index: int = 1,
        min_area: float = 0.0,
        max_count: int = 60,
    ) -> Dict[str, Any]:
        """
        Compact face table: '[i] Plane A=90000 c=(0.0,225.0,12.5) n=(0.0,0.0,1.0)' one per line.
        min_area in mm². body_index is 1-based.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            bodies = get_attr_or_call(doc, "GetBodies2", 0, False) or []
            if not bodies:
                return self._result(False, "No solid bodies in active document.", SwErrors.swInvalidCall)
            if body_index < 1 or body_index > len(bodies):
                return self._result(
                    False,
                    f"body_index={body_index} out of range (1..{len(bodies)})",
                    SwErrors.swInvalidParameter,
                )

            body = bodies[body_index - 1]
            faces = get_attr_or_call(body, "GetFaces") or []
            lines = []
            items = []

            for idx, f in enumerate(faces, start=1):
                try:
                    area_m2 = float(get_attr_or_call(f, "GetArea") or 0.0)
                    area_mm2 = area_m2 * 1e6
                    if area_mm2 < min_area:
                        continue

                    surf = get_attr_or_call(f, "GetSurface")
                    stype = "Surf"
                    if surf:
                        if get_attr_or_call(surf, "IsPlane"):
                            stype = "Plane"
                        elif get_attr_or_call(surf, "IsCylinder"):
                            stype = "Cyl"
                        elif get_attr_or_call(surf, "IsCone"):
                            stype = "Cone"
                        elif get_attr_or_call(surf, "IsSphere"):
                            stype = "Sphere"
                        elif get_attr_or_call(surf, "IsTorus"):
                            stype = "Torus"

                    box = get_attr_or_call(f, "GetBox")
                    cx, cy, cz = 0.0, 0.0, 0.0
                    if box and len(box) >= 6:
                        cx = (box[0] + box[3]) * 500.0
                        cy = (box[1] + box[4]) * 500.0
                        cz = (box[2] + box[5]) * 500.0

                    norm = get_attr_or_call(f, "Normal")
                    n_str = ""
                    if norm and len(norm) >= 3 and stype == "Plane":
                        n_str = f" n=({norm[0]:.2f},{norm[1]:.2f},{norm[2]:.2f})"

                    line = f"[{idx}] {stype} A={area_mm2:.0f} c=({cx:.1f},{cy:.1f},{cz:.1f}){n_str}"
                    lines.append(line)
                    items.append({
                        "index": idx,
                        "type": stype,
                        "area_mm2": round(area_mm2, 1),
                        "centroid_mm": [round(cx, 2), round(cy, 2), round(cz, 2)],
                        "normal": [round(float(norm[0]), 3), round(float(norm[1]), 3), round(float(norm[2]), 3)] if (norm and len(norm) >= 3) else None,
                    })
                except Exception:
                    lines.append(f"[{idx}] ?")
                if len(lines) >= max_count:
                    lines.append(f"... truncated at {max_count}")
                    break

            table_str = "\n".join(lines) if lines else "(no faces match)"
            return self._result(
                True,
                table_str,
                SwErrors.swSuccess,
                {"table": table_str, "count": len(items), "faces": items},
            )
        except Exception as e:
            logger.error(f"list_faces error: {e}")
            return self._result(False, f"list_faces error: {e}", SwErrors.swGeneralError)

    def list_edges(
        self,
        body_index: int = 1,
        min_length: float = 0.0,
        max_count: int = 60,
    ) -> Dict[str, Any]:
        """
        Compact edge table: '[i] L=200.0 mid=(0.0,-225.0,0.8)' one per line.
        min_length filters out tiny edges (mm). body_index is 1-based.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            bodies = get_attr_or_call(doc, "GetBodies2", 0, False) or []
            if not bodies:
                return self._result(False, "No solid bodies in active document.", SwErrors.swInvalidCall)
            if body_index < 1 or body_index > len(bodies):
                return self._result(
                    False,
                    f"body_index={body_index} out of range (1..{len(bodies)})",
                    SwErrors.swInvalidParameter,
                )

            body = bodies[body_index - 1]
            edges = get_attr_or_call(body, "GetEdges") or []
            lines = []
            items = []

            for idx, e in enumerate(edges, start=1):
                try:
                    v1 = get_attr_or_call(e, "GetStartVertex")
                    v2 = get_attr_or_call(e, "GetEndVertex")
                    pt1 = get_attr_or_call(v1, "GetPoint") if v1 else None
                    pt2 = get_attr_or_call(v2, "GetPoint") if v2 else None

                    if pt1 and pt2:
                        length_mm = math.sqrt(sum((a - b) ** 2 for a, b in zip(pt1, pt2))) * 1000.0
                        if length_mm < min_length:
                            continue
                        mx = (pt1[0] + pt2[0]) * 500.0
                        my = (pt1[1] + pt2[1]) * 500.0
                        mz = (pt1[2] + pt2[2]) * 500.0
                        lines.append(f"[{idx}] L={length_mm:.1f} mid=({mx:.1f},{my:.1f},{mz:.1f})")
                        items.append({
                            "index": idx,
                            "length_mm": round(length_mm, 2),
                            "midpoint_mm": [round(mx, 2), round(my, 2), round(mz, 2)],
                        })
                    else:
                        curve = get_attr_or_call(e, "GetCurve")
                        cparams = get_attr_or_call(curve, "CircleParams") if curve else None
                        if cparams and len(cparams) >= 7:
                            r_mm = float(cparams[6]) * 1000.0
                            length_mm = 2.0 * math.pi * r_mm
                            if length_mm < min_length:
                                continue
                            cx, cy, cz = cparams[0] * 1000.0, cparams[1] * 1000.0, cparams[2] * 1000.0
                            lines.append(f"[{idx}] Circle R={r_mm:.1f} L={length_mm:.1f} c=({cx:.1f},{cy:.1f},{cz:.1f})")
                            items.append({
                                "index": idx,
                                "length_mm": round(length_mm, 2),
                                "radius_mm": round(r_mm, 2),
                                "midpoint_mm": [round(cx, 2), round(cy, 2), round(cz, 2)],
                            })
                        else:
                            lines.append(f"[{idx}] (closed curve)")
                except Exception:
                    lines.append(f"[{idx}] ?")
                if len(lines) >= max_count:
                    lines.append(f"... truncated at {max_count}")
                    break

            table_str = "\n".join(lines) if lines else "(no edges match)"
            return self._result(
                True,
                table_str,
                SwErrors.swSuccess,
                {"table": table_str, "count": len(items), "edges": items},
            )
        except Exception as e:
            logger.error(f"list_edges error: {e}")
            return self._result(False, f"list_edges error: {e}", SwErrors.swGeneralError)

    def find_face(
        self,
        body_index: int = 1,
        x: Optional[float] = None,
        y: Optional[float] = None,
        z: Optional[float] = None,
        min_area: float = 10.0,
        surface_type: Optional[str] = "plane",
        tolerance: float = 0.5,
        select: bool = True,
    ) -> Dict[str, Any]:
        """
        Locate a body face by its centroid coordinates (mm) and optionally select it.
        Returns 1-based face index.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            bodies = get_attr_or_call(doc, "GetBodies2", 0, False) or []
            if not bodies or body_index < 1 or body_index > len(bodies):
                return self._result(False, "Invalid body_index or no solid bodies.", SwErrors.swInvalidParameter)

            body = bodies[body_index - 1]
            faces = get_attr_or_call(body, "GetFaces") or []
            stype_req = (surface_type or "").strip().lower()

            for idx, f in enumerate(faces, start=1):
                try:
                    area_mm2 = float(get_attr_or_call(f, "GetArea") or 0.0) * 1e6
                    if area_mm2 < min_area:
                        continue
                    surf = get_attr_or_call(f, "GetSurface")
                    if stype_req and surf:
                        if stype_req in ("plane", "5890") and not get_attr_or_call(surf, "IsPlane"):
                            continue
                        elif stype_req in ("cylinder", "cyl", "5891") and not get_attr_or_call(surf, "IsCylinder"):
                            continue

                    box = get_attr_or_call(f, "GetBox")
                    if not box or len(box) < 6:
                        continue
                    cx = (box[0] + box[3]) * 500.0
                    cy = (box[1] + box[4]) * 500.0
                    cz = (box[2] + box[5]) * 500.0

                    if x is not None and abs(cx - x) > tolerance:
                        continue
                    if y is not None and abs(cy - y) > tolerance:
                        continue
                    if z is not None and abs(cz - z) > tolerance:
                        continue

                    if select:
                        try:
                            doc.ClearSelection2(True)
                            f.Select2(False, 0)
                        except Exception:
                            pass

                    return self._result(
                        True,
                        f"Found face [{idx}] at ({cx:.1f},{cy:.1f},{cz:.1f})mm (A={area_mm2:.0f}mm²)",
                        SwErrors.swSuccess,
                        {
                            "face_index": idx,
                            "centroid_mm": [round(cx, 2), round(cy, 2), round(cz, 2)],
                            "area_mm2": round(area_mm2, 1),
                            "selected": select,
                        },
                    )
                except Exception:
                    continue

            return self._result(
                False,
                f"No face matching centroid (x={x}, y={y}, z={z}) area>={min_area} tol={tolerance}mm",
                SwErrors.swSelectionError,
            )
        except Exception as e:
            logger.error(f"find_face error: {e}")
            return self._result(False, f"find_face error: {e}", SwErrors.swGeneralError)

    def find_edge(
        self,
        body_index: int = 1,
        x: Optional[float] = None,
        y: Optional[float] = None,
        z: Optional[float] = None,
        tolerance: float = 0.5,
        select: bool = True,
    ) -> Dict[str, Any]:
        """
        Locate a body edge by its midpoint coordinates (mm) and optionally select it.
        Returns 1-based edge index.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            bodies = get_attr_or_call(doc, "GetBodies2", 0, False) or []
            if not bodies or body_index < 1 or body_index > len(bodies):
                return self._result(False, "Invalid body_index or no solid bodies.", SwErrors.swInvalidParameter)

            body = bodies[body_index - 1]
            edges = get_attr_or_call(body, "GetEdges") or []

            for idx, e in enumerate(edges, start=1):
                try:
                    v1 = get_attr_or_call(e, "GetStartVertex")
                    v2 = get_attr_or_call(e, "GetEndVertex")
                    pt1 = get_attr_or_call(v1, "GetPoint") if v1 else None
                    pt2 = get_attr_or_call(v2, "GetPoint") if v2 else None
                    if not pt1 or not pt2:
                        continue
                    mx = (pt1[0] + pt2[0]) * 500.0
                    my = (pt1[1] + pt2[1]) * 500.0
                    mz = (pt1[2] + pt2[2]) * 500.0

                    if x is not None and abs(mx - x) > tolerance:
                        continue
                    if y is not None and abs(my - y) > tolerance:
                        continue
                    if z is not None and abs(mz - z) > tolerance:
                        continue

                    if select:
                        try:
                            doc.ClearSelection2(True)
                            e.Select2(False, 0)
                        except Exception:
                            pass

                    return self._result(
                        True,
                        f"Found edge [{idx}] at midpoint ({mx:.1f},{my:.1f},{mz:.1f})mm",
                        SwErrors.swSuccess,
                        {
                            "edge_index": idx,
                            "midpoint_mm": [round(mx, 2), round(my, 2), round(mz, 2)],
                            "selected": select,
                        },
                    )
                except Exception:
                    continue

            return self._result(
                False,
                f"No edge found matching midpoint (x={x}, y={y}, z={z}) tol={tolerance}mm",
                SwErrors.swSelectionError,
            )
        except Exception as e:
            logger.error(f"find_edge error: {e}")
            return self._result(False, f"find_edge error: {e}", SwErrors.swGeneralError)

    # Named appearance preset library (matching lminventor's appearance names)
    _APPEARANCE_PRESETS: Dict[str, Tuple[List[int], float, float, float]] = {
        # name_lower: ([R, G, B], ambient, specular, transparency)
        "smooth - white": ([245, 245, 242], 0.6, 0.3, 0.0),
        "white": ([245, 245, 242], 0.6, 0.3, 0.0),
        "gold": ([212, 175, 55], 0.4, 0.9, 0.0),
        "brass": ([181, 166, 66], 0.4, 0.85, 0.0),
        "copper": ([184, 115, 51], 0.4, 0.85, 0.0),
        "aluminum - polished": ([210, 215, 220], 0.4, 0.9, 0.0),
        "aluminum": ([195, 200, 205], 0.45, 0.7, 0.0),
        "stainless steel": ([180, 185, 190], 0.4, 0.85, 0.0),
        "steel": ([140, 145, 150], 0.4, 0.65, 0.0),
        "anodized black": ([35, 38, 42], 0.3, 0.5, 0.0),
        "black": ([30, 30, 32], 0.3, 0.4, 0.0),
        "clear glass": ([210, 235, 245], 0.5, 0.95, 0.75),
        "glass": ([210, 235, 245], 0.5, 0.95, 0.75),
        "red": ([210, 45, 45], 0.45, 0.5, 0.0),
        "blue": ([40, 110, 215], 0.45, 0.5, 0.0),
        "green": ([45, 165, 80], 0.45, 0.5, 0.0),
    }

    def apply_material_appearance(
        self,
        appearance_name: str = "Smooth - White",
        color_rgb: Optional[List[int]] = None,
        reflectivity: Optional[float] = None,
        transparency: Optional[float] = None,
        target_type: str = "part",
        target_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Apply a named material appearance or custom RGB/optical properties to the active part,
        feature, or component occurrence.
        Matches lminventor's apply_material_appearance tool.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            preset_key = (appearance_name or "Smooth - White").strip().lower()
            preset = self._APPEARANCE_PRESETS.get(preset_key)
            if preset is None:
                # Substring match fallback
                for k, v in self._APPEARANCE_PRESETS.items():
                    if k in preset_key or preset_key in k:
                        preset = v
                        break
            if preset is None:
                preset = ([200, 205, 210], 0.45, 0.6, 0.0)

            rgb = color_rgb if (color_rgb and len(color_rgb) == 3) else preset[0]
            ambient = preset[1]
            spec = float(reflectivity) if reflectivity is not None else preset[2]
            transp = float(transparency) if transparency is not None else preset[3]

            # SolidWorks MaterialPropertyValues:
            # [R, G, B, Ambient, Diffuse, Specular, Shininess, Transparency, Emission]
            mat_vals = [
                max(0.0, min(1.0, rgb[0] / 255.0)),
                max(0.0, min(1.0, rgb[1] / 255.0)),
                max(0.0, min(1.0, rgb[2] / 255.0)),
                ambient,
                0.8,
                max(0.0, min(1.0, spec)),
                max(0.1, min(1.0, spec)),
                max(0.0, min(1.0, transp)),
                0.0,
            ]

            ttype = (target_type or "part").strip().lower()
            if ttype == "feature" and target_name:
                feat = self._find_feature_by_name(doc, target_name) if hasattr(self, "_find_feature_by_name") else None
                if feat is None:
                    return self._result(False, f"Feature '{target_name}' not found.", SwErrors.swFeatureError)
                feat.SetMaterialPropertyValues2(mat_vals, 1, None)
            elif ttype in ("occurrence", "component") and target_name:
                comps = get_attr_or_call(doc, "GetComponents", False) or []
                target_comp = None
                for c in comps:
                    if str(getattr(c, "Name2", "")).lower() == target_name.lower():
                        target_comp = c
                        break
                if target_comp is None:
                    return self._result(False, f"Component '{target_name}' not found.", SwErrors.swSelectionError)
                target_comp.MaterialPropertyValues = mat_vals
            else:
                doc.MaterialPropertyValues = mat_vals

            try:
                doc.GraphicsRedraw2()
            except Exception:
                pass

            return self._result(
                True,
                f"Applied appearance '{appearance_name}' (RGB={rgb}, specular={spec:.2f}, transparency={transp:.2f}) to {ttype}",
                SwErrors.swSuccess,
                {
                    "appearance_name": appearance_name,
                    "color_rgb": rgb,
                    "specular": spec,
                    "transparency": transp,
                    "target_type": ttype,
                    "target_name": target_name,
                },
            )
        except Exception as e:
            logger.error(f"apply_material_appearance error: {e}")
            return self._result(False, f"apply_material_appearance error: {e}", SwErrors.swGeneralError)

    def render_showcase_viewport(
        self,
        eye_mm: Optional[List[float]] = None,
        target_mm: Optional[List[float]] = None,
        up_vec: Optional[List[float]] = None,
        display_style: str = "shaded_with_edges",
        filepath: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Set display mode ('shaded_with_edges', 'shaded', 'realistic', 'wireframe', 'hidden_lines_removed'),
        orient camera via eye_mm/target_mm/up_vec (or isometric fit), and capture viewport PNG.
        Matches lminventor's render_showcase_viewport tool.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            view = getattr(doc, "ActiveView", None)
            style_clean = (display_style or "shaded_with_edges").strip().lower()
            # swViewDisplayMode_e: 1=Wireframe, 2=HiddenLinesGrayed, 3=HiddenLinesRemoved, 4=Shaded, 5=ShadedWithEdges
            style_map = {
                "wireframe": 1,
                "hidden_lines_visible": 2,
                "hidden_lines_removed": 3,
                "shaded": 4,
                "realistic": 4,
                "shaded_with_edges": 5,
            }
            if view is not None:
                try:
                    view.DisplayMode = style_map.get(style_clean, 5)
                except Exception:
                    pass

            if eye_mm and target_mm and len(eye_mm) == 3 and len(target_mm) == 3:
                try:
                    # Orient via ShowNamedView2 + zoom to fit first, then adjust camera if available
                    doc.ShowNamedView2("*Isometric", 7)
                    doc.ViewZoomtofit2()
                    cam = getattr(view, "Camera", None) if view else None
                    if cam is not None:
                        cam.SetPositionCartesian(eye_mm[0] / 1000.0, eye_mm[1] / 1000.0, eye_mm[2] / 1000.0)
                        cam.SetTargetPointCartesian(target_mm[0] / 1000.0, target_mm[1] / 1000.0, target_mm[2] / 1000.0)
                        if up_vec and len(up_vec) == 3:
                            cam.SetUpVector(up_vec[0], up_vec[1], up_vec[2])
                except Exception as cam_ex:
                    logger.debug(f"Camera custom positioning fallback: {cam_ex}")

            cap_res = self.capture_viewport(filepath=filepath, view_name="*Isometric")
            if cap_res.get("success") and isinstance(cap_res.get("data"), dict):
                cap_res["data"]["display_style"] = style_clean
                cap_res["data"]["eye_mm"] = eye_mm
                cap_res["data"]["target_mm"] = target_mm
            return cap_res
        except Exception as e:
            logger.error(f"render_showcase_viewport error: {e}")
            return self._result(False, f"render_showcase_viewport error: {e}", SwErrors.swGeneralError)
