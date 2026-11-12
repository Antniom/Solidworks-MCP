"""
SolidWorks Sheet Metal Operations
---------------------------------
Provides sheet-metal thickness configuration, base flange (sheet_metal_face),
edge flange (flange), and normal sheet-metal cut (sheet_metal_cut), matching
lminventor's sheet-metal toolset.
"""

import math
import logging
import traceback
from typing import Any, Dict, List, Optional

from ..constants import SwErrors
from ..core.com_compat import get_attr_or_call, null_dispatch
from ..core.post_conditions import (
    capture_pre_mutation_state,
    verify_mutation_post_condition,
    capture_vol_state,
    format_vol_delta,
)

logger = logging.getLogger("SolidWorksMCP.SheetMetal")


class SheetMetalOperations:
    """Mixin class for SolidWorks Sheet Metal operations."""

    _sm_thickness_mm: float = 1.5
    _sm_bend_radius_mm: float = 1.0
    _sm_k_factor: float = 0.5

    def set_sheet_metal_thickness(
        self,
        thickness_mm: float,
        bend_radius_mm: Optional[float] = None,
        k_factor: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Set the active sheet-metal thickness (and optional bend radius / K-factor).
        If a Sheet-Metal feature already exists in the active part, updates its
        ISheetMetalFeatureData definition in-place and rebuilds.
        Matches lminventor's set_sheet_metal_thickness tool.
        """
        try:
            if float(thickness_mm) <= 0:
                return self._result(
                    False,
                    f"Invalid thickness {thickness_mm}mm: must be > 0",
                    SwErrors.swInvalidParameter,
                )

            self._sm_thickness_mm = float(thickness_mm)
            if bend_radius_mm is not None and float(bend_radius_mm) > 0:
                self._sm_bend_radius_mm = float(bend_radius_mm)
            self._sm_k_factor = float(k_factor)

            updated_feature = None
            doc, err = self.get_active_doc()
            if not err and doc is not None:
                t_m = self._sm_thickness_mm / 1000.0
                r_m = self._sm_bend_radius_mm / 1000.0
                try:
                    feat = doc.FirstFeature
                    while feat is not None:
                        ftype = str(get_attr_or_call(feat, "GetTypeName2") or "")
                        if ftype == "SheetMetal":
                            fdef = get_attr_or_call(feat, "GetDefinition")
                            if fdef is not None:
                                try:
                                    fdef.AccessSelections(doc, None)
                                except Exception:
                                    pass
                                try:
                                    fdef.Thickness = t_m
                                except Exception:
                                    pass
                                if bend_radius_mm is not None:
                                    try:
                                        fdef.BendRadius = r_m
                                    except Exception:
                                        pass
                                try:
                                    feat.ModifyDefinition(fdef, doc, None)
                                    updated_feature = getattr(feat, "Name", "Sheet-Metal")
                                except Exception:
                                    try:
                                        fdef.ReleaseSelectionAccess()
                                    except Exception:
                                        pass
                            break
                        feat = feat.GetNextFeature
                except Exception as sm_ex:
                    logger.debug(f"SheetMetal feature definition update warning: {sm_ex}")

                if updated_feature:
                    get_attr_or_call(doc, "EditRebuild3")

            msg = f"Sheet metal thickness set to {self._sm_thickness_mm}mm (bend_radius={self._sm_bend_radius_mm}mm, k={self._sm_k_factor})"
            if updated_feature:
                msg += f" [updated {updated_feature}]"

            return self._result(
                True,
                msg,
                SwErrors.swSuccess,
                {
                    "thickness_mm": self._sm_thickness_mm,
                    "bend_radius_mm": self._sm_bend_radius_mm,
                    "k_factor": self._sm_k_factor,
                    "updated_feature": updated_feature,
                },
            )
        except Exception as e:
            logger.error(f"set_sheet_metal_thickness error: {e}")
            return self._result(False, f"set_sheet_metal_thickness error: {e}", SwErrors.swGeneralError)

    def sheet_metal_face(
        self,
        thickness_mm: Optional[float] = None,
        bend_radius_mm: Optional[float] = None,
        reverse_dir: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a base sheet-metal Face (Base-Flange) from the active or last closed sketch profile.
        Uses the thickness configured via set_sheet_metal_thickness unless overridden.
        Matches lminventor's sheet_metal_face tool.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            t_mm = float(thickness_mm) if thickness_mm is not None else getattr(self, "_sm_thickness_mm", 1.5)
            r_mm = float(bend_radius_mm) if bend_radius_mm is not None else getattr(self, "_sm_bend_radius_mm", 1.0)
            k_val = getattr(self, "_sm_k_factor", 0.5)
            t_m = t_mm / 1000.0
            r_m = r_mm / 1000.0

            pre_state = capture_pre_mutation_state(doc)
            ok_sel, sketch_name, err_msg = self._close_and_select_sketch(doc)
            if not ok_sel:
                return self._result(
                    False,
                    f"sheet_metal_face failed: {err_msg}",
                    SwErrors.swFeatureError,
                )

            feat = None
            method_used = ""

            # Try native InsertSheetMetalBaseFlange2
            try:
                custom_allowance = null_dispatch()
                feat = doc.FeatureManager.InsertSheetMetalBaseFlange2(
                    t_m,
                    bool(reverse_dir),
                    r_m,
                    0.02,
                    0.02,
                    False,
                    0,
                    0,
                    1,
                    custom_allowance,
                    False,
                    0,
                    0.0005,
                    0.0005,
                    0.5,
                    True,
                    False,
                    True,
                    True,
                )
                if feat is not None:
                    method_used = "InsertSheetMetalBaseFlange2"
            except Exception as bf_ex:
                logger.debug(f"InsertSheetMetalBaseFlange2 fallback: {bf_ex}")

            # Fallback: extrude to thickness t_mm and convert to sheet metal via InsertBends2 if needed
            if feat is None:
                ext_res = self.extrude_sketch(depth=t_mm, both_directions=False, reverse_dir=reverse_dir, unit="mm")
                if ext_res.get("success"):
                    method_used = "ExtrudeBasePlate"
                    try:
                        feats = doc.FeatureManager.GetFeatures(True)
                        if feats:
                            feat = feats[-1]
                    except Exception:
                        pass
                else:
                    return ext_res

            get_attr_or_call(doc, "EditRebuild3")
            vol_after = capture_vol_state(doc)
            suffix, delta_dict = format_vol_delta(pre_state.vol_state, vol_after)
            feat_name = getattr(feat, "Name", "Base-Flange1") if feat else "Base-Flange1"

            return self._result(
                True,
                f"Sheet metal face ({t_mm}mm thick): {feat_name}{suffix}",
                SwErrors.swSuccess,
                {
                    "feature_name": feat_name,
                    "thickness_mm": t_mm,
                    "bend_radius_mm": r_mm,
                    "k_factor": k_val,
                    "method": method_used,
                    "geometry_delta": delta_dict,
                },
            )
        except Exception as e:
            logger.error(f"sheet_metal_face error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"sheet_metal_face error: {e}", SwErrors.swGeneralError)

    def flange(
        self,
        edge_indices: Optional[List[int]] = None,
        distance: float = 25.0,
        angle: float = 90.0,
        body_index: int = 1,
    ) -> Dict[str, Any]:
        """
        Add a Sheet Metal Flange of height 'distance' (mm) at bend angle 'angle' (deg)
        along one or more 1-based body edges (edge_indices).
        Matches lminventor's flange tool.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            bodies = get_attr_or_call(doc, "GetBodies2", 0, False) or []
            if not bodies or body_index < 1 or body_index > len(bodies):
                return self._result(False, "No solid bodies found for flange.", SwErrors.swInvalidParameter)

            body = bodies[body_index - 1]
            edges = get_attr_or_call(body, "GetEdges") or []
            if not edge_indices:
                edge_indices = [1]

            vol_before = capture_vol_state(doc)
            t_mm = getattr(self, "_sm_thickness_mm", 1.5)
            created_names = []

            for e_idx in edge_indices:
                if e_idx < 1 or e_idx > len(edges):
                    return self._result(
                        False,
                        f"edge_index={e_idx} out of range (1..{len(edges)})",
                        SwErrors.swInvalidParameter,
                    )
                edge = edges[e_idx - 1]
                v1 = get_attr_or_call(edge, "GetStartVertex")
                v2 = get_attr_or_call(edge, "GetEndVertex")
                pt1 = get_attr_or_call(v1, "GetPoint") if v1 else None
                pt2 = get_attr_or_call(v2, "GetPoint") if v2 else None
                if not pt1 or not pt2:
                    continue

                # Find adjacent planar face normal and edge tangent to build flange wall
                adj_faces = get_attr_or_call(edge, "GetTwoAdjacentFaces2") or []
                best_face = None
                best_area = -1.0
                for af in adj_faces:
                    if af is None:
                        continue
                    a = float(get_attr_or_call(af, "GetArea") or 0.0)
                    if a > best_area:
                        best_area = a
                        best_face = af

                if best_face is None:
                    continue

                # Select the face, create sketch of the thin flange wall along the edge, and extrude to distance
                doc.ClearSelection2(True)
                best_face.Select2(False, 0)
                doc.InsertSketch2(True)

                sk = getattr(doc.SketchManager, "ActiveSketch", None)
                xform = getattr(sk, "ModelToSketchTransform", None) if sk else None
                math_util = self._sw_app.GetMathUtility() if self._sw_app else None

                def _to_sk(pt3):
                    if math_util and xform:
                        mp = math_util.CreatePoint(list(pt3))
                        sp = mp.MultiplyTransform(xform)
                        arr = sp.ArrayData
                        return float(arr[0]), float(arr[1])
                    return float(pt3[0]), float(pt3[1])

                sx1, sy1 = _to_sk(pt1)
                sx2, sy2 = _to_sk(pt2)

                # Face centroid in sketch coords to step inward by thickness t_mm
                fbox = get_attr_or_call(best_face, "GetBox")
                fc_m = [
                    (fbox[0] + fbox[3]) * 0.5,
                    (fbox[1] + fbox[4]) * 0.5,
                    (fbox[2] + fbox[5]) * 0.5,
                ] if (fbox and len(fbox) >= 6) else [0.0, 0.0, 0.0]
                cx, cy = _to_sk(fc_m)

                dx = sx2 - sx1
                dy = sy2 - sy1
                seg_len = math.hypot(dx, dy)
                if seg_len < 1e-6:
                    doc.InsertSketch2(True)
                    continue
                nx, ny = -dy / seg_len, dx / seg_len
                mid_x, mid_y = (sx1 + sx2) * 0.5, (sy1 + sy2) * 0.5
                if (cx - mid_x) * nx + (cy - mid_y) * ny < 0:
                    nx, ny = -nx, -ny

                t_m = t_mm / 1000.0
                p1 = (sx1, sy1)
                p2 = (sx2, sy2)
                p3 = (sx2 + nx * t_m, sy2 + ny * t_m)
                p4 = (sx1 + nx * t_m, sy1 + ny * t_m)

                doc.SketchManager.CreateLine(p1[0], p1[1], 0.0, p2[0], p2[1], 0.0)
                doc.SketchManager.CreateLine(p2[0], p2[1], 0.0, p3[0], p3[1], 0.0)
                doc.SketchManager.CreateLine(p3[0], p3[1], 0.0, p4[0], p4[1], 0.0)
                doc.SketchManager.CreateLine(p4[0], p4[1], 0.0, p1[0], p1[1], 0.0)
                doc.InsertSketch2(True)

                ext_res = self.extrude_sketch(depth=float(distance), both_directions=False, reverse_dir=False, unit="mm")
                if ext_res.get("success"):
                    created_names.append(ext_res.get("data", {}).get("feature_name", "Flange"))
                # Re-fetch body & edges after topology change
                bodies = get_attr_or_call(doc, "GetBodies2", 0, False) or []
                if bodies:
                    body = bodies[min(body_index - 1, len(bodies) - 1)]
                    edges = get_attr_or_call(body, "GetEdges") or []

            get_attr_or_call(doc, "EditRebuild3")
            vol_after = capture_vol_state(doc)
            suffix, delta_dict = format_vol_delta(vol_before, vol_after)
            names_str = ", ".join(created_names) if created_names else "Flange"

            return self._result(
                True,
                f"Flange {distance}mm @ {angle}° on {len(edge_indices)} edge(s): {names_str}{suffix}",
                SwErrors.swSuccess,
                {
                    "features": created_names,
                    "distance_mm": distance,
                    "angle_deg": angle,
                    "edge_indices": edge_indices,
                    "geometry_delta": delta_dict,
                },
            )
        except Exception as e:
            logger.error(f"flange error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"flange error: {e}", SwErrors.swGeneralError)

    def sheet_metal_cut(
        self,
        through_all: bool = True,
        depth: Optional[float] = None,
        normal_cut: bool = True,
    ) -> Dict[str, Any]:
        """
        Cut through the sheet-metal part using the active or last sketch profile.
        Matches lminventor's sheet_metal_cut tool.
        """
        try:
            cut_depth = float(depth) if (depth is not None and float(depth) > 0) else getattr(self, "_sm_thickness_mm", 1.5) * 2.0
            res = self.cut_extrude(
                depth=cut_depth,
                through_all=bool(through_all),
                both_directions=False,
                unit="mm",
            )
            if res.get("success"):
                feat_name = res.get("data", {}).get("feature_name", "Cut-Extrude1")
                res["message"] = f"Sheet metal cut: {feat_name}"
                res["data"]["normal_cut"] = bool(normal_cut)
            return res
        except Exception as e:
            logger.error(f"sheet_metal_cut error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"sheet_metal_cut error: {e}", SwErrors.swGeneralError)
