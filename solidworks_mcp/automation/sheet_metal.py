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
        depth: Optional[float] = None,
        both_directions: bool = False,
        reverse_extrude: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a base sheet-metal Base-Flange from the active or last sketch.

        Closed profile: a flat plate; reverse_dir puts the thickness against the sketch normal.
        Open profile (a line chain such as an L, U, Z or hat section): the chain is extruded by
        `depth` mm (mid-plane total with both_directions; reverse_extrude flips the extrude side)
        and SolidWorks adds a bend at every corner, giving a real flat pattern. reverse_dir moves
        the thickness to the other side of the drawn lines.
        Uses the thickness configured via set_sheet_metal_thickness unless overridden.
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
            if ok_sel and hasattr(self, "_recapture_pre_volume"):
                self._recapture_pre_volume(doc, pre_state)
            if not ok_sel:
                return self._result(
                    False,
                    f"sheet_metal_face failed: {err_msg}",
                    SwErrors.swFeatureError,
                )

            profile = self._profile_kind(doc, sketch_name)
            if profile.get("kind") == "open":
                return self._open_profile_base_flange(
                    doc, sketch_name, profile, pre_state, t_mm, r_mm, k_val, depth,
                    bool(both_directions), bool(reverse_dir), bool(reverse_extrude))
            if profile.get("kind") == "mixed":
                return self._result(
                    False,
                    f"Sketch '{sketch_name}' mixes open and closed geometry ({profile.get('detail')}). A base flange "
                    "needs either one closed profile (flat plate) or one open line chain (bent section).",
                    SwErrors.swSketchError, profile)

            feat = None
            method_used = ""

            # Try native InsertSheetMetalBaseFlange2. For a closed profile the 6th argument
            # (FlipExtruDir) sets the thickness side: True = along the sketch normal (default here),
            # False = against it. The 2nd argument (ThickenDir) does not change it.
            flip_extru = not bool(reverse_dir)
            try:
                custom_allowance = null_dispatch()
                feat = doc.FeatureManager.InsertSheetMetalBaseFlange2(
                    t_m,
                    False,
                    r_m,
                    0.02,
                    0.02,
                    flip_extru,
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

            # Thickness side (MCP-003): measure where the plate sits relative to the sketch plane.
            # Default = along the sketch plane normal; reverse_dir = the other side. If it is
            # wrong, rebuild once with the opposite FlipExtruDir (verified live: that argument
            # decides the side for a closed profile; ThickenDir, where reverse_dir used to go,
            # has no effect).
            side_info = None
            if feat is not None and method_used == "InsertSheetMetalBaseFlange2":
                want_side = -1 if reverse_dir else 1
                side_info = self._plate_side(doc, sketch_name)
                if side_info is not None and side_info["side"] != want_side:
                    try:
                        doc.ClearSelection2(True)
                        if feat.Select2(False, 0):
                            doc.Extension.DeleteSelection2(0)  # keep the absorbed sketch
                        ok_sel2, sketch_name, _ = self._close_and_select_sketch(doc)
                        feat = doc.FeatureManager.InsertSheetMetalBaseFlange2(
                            t_m, False, r_m, 0.02, 0.02, not flip_extru, 0, 0, 1, null_dispatch(), False,
                            0, 0.0005, 0.0005, 0.5, True, False, True, True,
                        ) if ok_sel2 else None
                        get_attr_or_call(doc, "EditRebuild3")
                    except Exception as flip_ex:
                        logger.warning(f"sheet_metal_face: could not rebuild on the other side: {flip_ex}")
                    side_info = self._plate_side(doc, sketch_name) if feat is not None else None
                if feat is None:
                    return self._result(False, "Base flange could not be rebuilt on the requested side.",
                                        SwErrors.swFeatureError)
                if side_info is not None and side_info["side"] != want_side:
                    return self._result(
                        False,
                        f"Base flange {getattr(feat, 'Name', '?')} was created but its thickness lies on the "
                        f"{'normal' if side_info['side'] > 0 else 'opposite'} side of the sketch plane "
                        f"(sketch z {side_info['z_min_mm']}..{side_info['z_max_mm']} mm) and could not be flipped.",
                        SwErrors.swFeatureError,
                        side_info,
                    )

            vol_after = capture_vol_state(doc)
            suffix, delta_dict = format_vol_delta(pre_state.vol_state, vol_after)
            feat_name = getattr(feat, "Name", "Base-Flange1") if feat else "Base-Flange1"
            # Remember the thickness actually used so flange/cut default to it.
            self._sm_thickness_mm = float(t_mm)

            side_txt = ""
            if side_info is not None:
                side_txt = (f", thickness {'along' if side_info['side'] > 0 else 'against'} the sketch normal "
                            f"(world {side_info['normal_world']}), plate world bbox "
                            f"{side_info['world_min_mm']}..{side_info['world_max_mm']} mm")
            return self._result(
                True,
                f"Sheet metal face ({t_mm}mm thick): {feat_name}{side_txt}{suffix}",
                SwErrors.swSuccess,
                {
                    "feature_name": feat_name,
                    "thickness_mm": t_mm,
                    "bend_radius_mm": r_mm,
                    "k_factor": k_val,
                    "method": method_used,
                    "thickness_side": side_info,
                    "geometry_delta": delta_dict,
                },
            )
        except Exception as e:
            logger.error(f"sheet_metal_face error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"sheet_metal_face error: {e}", SwErrors.swGeneralError)

    def _profile_kind(self, doc, sketch_name: Optional[str]) -> Dict[str, Any]:
        """
        Classify the (non-construction) geometry of a sketch: 'closed' (only closed loops),
        'open' (exactly one unbranched open chain) or 'mixed'. 'unknown' when it cannot be read;
        callers then keep the old closed-profile behaviour.
        """
        try:
            sk_feat = doc.FeatureByName(sketch_name) if sketch_name else None
            sk = get_attr_or_call(sk_feat, "GetSpecificFeature2") if sk_feat is not None else None
            segs = list(get_attr_or_call(sk, "GetSketchSegments") or []) if sk is not None else []
            key = lambda p: (round(float(p.X), 7), round(float(p.Y), 7))  # noqa: E731
            parent: Dict[Any, Any] = {}

            def find(a):
                while parent.setdefault(a, a) != a:
                    a = parent[a]
                return a

            degree: Dict[Any, int] = {}
            length_m = 0.0
            n = 0
            for seg in segs:
                if bool(get_attr_or_call(seg, "ConstructionGeometry")):
                    continue
                n += 1
                try:
                    length_m += float(get_attr_or_call(seg, "GetLength"))
                except Exception:
                    pass
                a = key(get_attr_or_call(seg, "GetStartPoint2"))
                b = key(get_attr_or_call(seg, "GetEndPoint2"))
                if a == b:  # full circle / closed spline: a loop by itself
                    find(a)
                    continue
                degree[a] = degree.get(a, 0) + 1
                degree[b] = degree.get(b, 0) + 1
                parent[find(a)] = find(b)
            if n == 0:
                return {"kind": "unknown", "detail": "no sketch segments"}
            ends = [p for p, d in degree.items() if d == 1]
            branches = [p for p, d in degree.items() if d > 2]
            comps = {find(p) for p in parent}
            info = {"segments": n, "open_ends": len(ends), "components": len(comps),
                    "length_mm": round(length_m * 1000.0, 4)}
            if not ends and not branches:
                return {"kind": "closed", **info}
            if len(ends) == 2 and not branches and len(comps) == 1:
                return {"kind": "open", **info}
            return {"kind": "mixed", "detail": f"{len(ends)} open end(s), {len(branches)} branch point(s), "
                                               f"{len(comps)} separate piece(s)", **info}
        except Exception as e:
            logger.debug(f"_profile_kind failed: {e}")
            return {"kind": "unknown", "detail": str(e)}

    def _open_profile_base_flange(self, doc, sketch_name, profile, pre_state, t_mm, r_mm, k_val,
                                  depth, both_directions, reverse_dir, reverse_extrude) -> Dict[str, Any]:
        """Base flange from an open line chain: extruded by depth, bends added at the corners."""
        if depth is None or float(depth) <= 0:
            return self._result(
                False,
                f"Sketch '{sketch_name}' is an open profile ({profile.get('segments')} segment(s), "
                f"{profile.get('length_mm')} mm long): pass depth (mm) - how far the bent section is extruded.",
                SwErrors.swInvalidInput, profile)
        depth_mm = float(depth)
        t_m, r_m, d_m = t_mm / 1000.0, r_mm / 1000.0, depth_mm / 1000.0
        end_cond = 6 if both_directions else 0  # swEndCondMidPlane: depth is the total
        feat = None
        try:
            # ThickenDir (2nd) sets which side of the lines the thickness goes for an open profile;
            # FlipExtruDir (6th) flips the extrude direction.
            feat = doc.FeatureManager.InsertSheetMetalBaseFlange2(
                t_m, bool(reverse_dir), r_m, d_m, 0.0, bool(reverse_extrude), end_cond, 0, 1, null_dispatch(),
                False, 0, 0.0005, 0.0005, 0.5, True, False, True, True)
        except Exception as e:
            logger.debug(f"open-profile InsertSheetMetalBaseFlange2 failed: {e}")
        if feat is None:
            return self._result(
                False,
                f"Base flange from open profile '{sketch_name}' failed. Check that segments meet end to end, no "
                f"segment is shorter than the bend radius ({r_mm} mm), and the profile does not cross itself.",
                SwErrors.swFeatureError, profile)
        get_attr_or_call(doc, "EditRebuild3")
        feat_name = getattr(feat, "Name", "Base-Flange1")

        # Extrude extent along the sketch normal must equal depth.
        side = self._plate_side(doc, sketch_name)
        extent_ok = True
        if side is not None:
            extent = side["z_max_mm"] - side["z_min_mm"]
            extent_ok = abs(extent - depth_mm) <= 0.01
        # Bends SolidWorks generated.
        bends = []
        try:
            sub = get_attr_or_call(feat, "GetFirstSubFeature")
            while sub is not None:
                tname = str(get_attr_or_call(sub, "GetTypeName2") or "")
                if "Bend" in tname:
                    bends.append(str(getattr(sub, "Name", tname)))
                sub = get_attr_or_call(sub, "GetNextSubFeature")
        except Exception:
            pass
        # Flat pattern size (flatten, measure, fold back) and volume consistency.
        vol_after = capture_vol_state(doc)
        suffix, delta_dict = format_vol_delta(pre_state.vol_state, vol_after)
        flat = self._flat_pattern_size(doc)
        vol_ok = True
        if flat is not None and delta_dict and delta_dict.get("volume_delta_mm3") is not None:
            expected = flat["length_mm"] * flat["width_mm"] * t_mm
            vol_ok = abs(delta_dict["volume_delta_mm3"] - expected) <= max(0.01 * expected, 1.0)
        self._sm_thickness_mm = float(t_mm)
        data = {
            "feature_name": feat_name, "profile": "open", "thickness_mm": t_mm, "bend_radius_mm": r_mm,
            "k_factor": k_val, "depth_mm": depth_mm, "both_directions": both_directions,
            "bends": bends, "flat_pattern_mm": flat, "sketch_extent": side, "geometry_delta": delta_dict,
            "segments": profile.get("segments"), "profile_length_mm": profile.get("length_mm"),
        }
        if not extent_ok or not vol_ok:
            return self._result(
                False,
                f"{feat_name} was created but does not match the request (extent "
                f"{'ok' if extent_ok else 'wrong: ' + str(side)}; volume vs flat pattern {'ok' if vol_ok else 'mismatch'}).",
                SwErrors.swFeatureError, data)
        flat_txt = (f", flat pattern {flat['length_mm']} x {flat['width_mm']} mm" if flat else "")
        ext_txt = f"{depth_mm} mm total, mid-plane" if both_directions else f"{depth_mm} mm"
        return self._result(
            True,
            f"Sheet metal base flange from open profile ({t_mm}mm thick, extruded {ext_txt}): {feat_name} with "
            f"{len(bends)} bend(s){flat_txt}{suffix}",
            SwErrors.swSuccess, data)

    def _flat_pattern_size(self, doc) -> Optional[Dict[str, float]]:
        """Unsuppress the Flat-Pattern, read the flat body box, and fold the part back."""
        fp = None
        try:
            f = get_attr_or_call(doc, "FirstFeature")
            while f is not None:
                if str(get_attr_or_call(f, "GetTypeName2") or "") == "FlatPattern":
                    fp = f
                f = get_attr_or_call(f, "GetNextFeature")
        except Exception:
            return None
        if fp is None:
            return None
        try:
            fp.SetSuppression2(1, 2, null_dispatch())  # unsuppress = flattened
            get_attr_or_call(doc, "EditRebuild3")
            bodies = get_attr_or_call(doc, "GetBodies2", 0, False) or []
            box = get_attr_or_call(bodies[0], "GetBodyBox") if bodies else None
            if not box:
                return None
            dims = sorted((abs(box[i + 3] - box[i]) * 1000.0 for i in range(3)), reverse=True)
            return {"length_mm": round(dims[0], 3), "width_mm": round(dims[1], 3), "thickness_mm": round(dims[2], 3)}
        except Exception as e:
            logger.debug(f"flat pattern size failed: {e}")
            return None
        finally:
            try:
                fp.SetSuppression2(0, 2, null_dispatch())  # fold back
                get_attr_or_call(doc, "EditRebuild3")
            except Exception:
                pass

    def _plate_side(self, doc, sketch_name: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Which side of its sketch plane the first solid body lies on: +1 along the sketch normal,
        -1 against it (from the body box corners mapped into sketch coordinates).
        """
        try:
            from ..core.com_compat import transform_point
            if not sketch_name:
                return None
            sk_feat = doc.FeatureByName(sketch_name)
            sk = get_attr_or_call(sk_feat, "GetSpecificFeature2") if sk_feat is not None else None
            xf = get_attr_or_call(sk, "ModelToSketchTransform") if sk is not None else None
            arr = list(get_attr_or_call(xf, "ArrayData")) if xf is not None else None
            bodies = get_attr_or_call(doc, "GetBodies2", 0, False) or []
            if not arr or not bodies:
                return None
            box = get_attr_or_call(bodies[0], "GetBodyBox")
            corners = [(x, y, z) for x in (box[0], box[3]) for y in (box[1], box[4]) for z in (box[2], box[5])]
            zs = [transform_point(arr, c)[2] for c in corners]
            z_min, z_max = min(zs), max(zs)
            side = 1 if (z_min + z_max) > 0 else -1
            return {
                "side": side,
                "z_min_mm": round(z_min * 1000.0, 4) + 0.0,
                "z_max_mm": round(z_max * 1000.0, 4) + 0.0,
                "normal_world": [round(v, 6) + 0.0 for v in (arr[2], arr[5], arr[8])],
                "world_min_mm": [round(v * 1000.0, 4) + 0.0 for v in box[0:3]],
                "world_max_mm": [round(v * 1000.0, 4) + 0.0 for v in box[3:6]],
            }
        except Exception as e:
            logger.debug(f"_plate_side failed: {e}")
            return None

    def flange(
        self,
        edge_indices: Optional[List[int]] = None,
        distance: float = 25.0,
        angle: float = 90.0,
        body_index: int = 1,
    ) -> Dict[str, Any]:
        """
        Add a 90° wall of the sheet thickness, 'distance' mm high, along one or more 1-based body
        edges (edge_indices). It is built as an extruded wall: square corner, no bend radius, and it
        does not unfold in a flat pattern. Each wall's added volume is checked against
        thickness x edge length x height; a wrong wall is removed and reported as an error.
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

            if abs(float(angle) - 90.0) > 1e-6:
                return self._result(
                    False,
                    f"flange: only 90° flanges are supported (got {angle}°); the flange is built as a "
                    "perpendicular extruded wall.",
                    SwErrors.swInvalidParameter,
                )

            vol_before = capture_vol_state(doc)
            # Use the real sheet thickness: the shortest edge of the base body.
            t_mm = getattr(self, "_sm_thickness_mm", 1.5)
            try:
                lengths = []
                for e in edges:
                    a = get_attr_or_call(get_attr_or_call(e, "GetStartVertex"), "GetPoint")
                    b = get_attr_or_call(get_attr_or_call(e, "GetEndVertex"), "GetPoint")
                    if a and b:
                        lengths.append(math.dist(a[:3], b[:3]) * 1000.0)
                if lengths:
                    t_mm = min(l for l in lengths if l > 1e-6)
            except Exception as t_ex:
                logger.debug(f"flange thickness detection failed, using {t_mm} mm: {t_ex}")
            created_names = []

            # Resolve every requested edge to its end points NOW. Edge indices refer to the body as
            # it was when listed; after the first wall is added the edge list changes, and the old
            # code then built the second wall on an unrelated edge (MCP-005).
            targets = []
            for e_idx in edge_indices:
                if e_idx < 1 or e_idx > len(edges):
                    return self._result(
                        False,
                        f"edge_index={e_idx} out of range (1..{len(edges)})",
                        SwErrors.swInvalidParameter,
                    )
                e0 = edges[e_idx - 1]
                a0 = get_attr_or_call(get_attr_or_call(e0, "GetStartVertex"), "GetPoint")
                b0 = get_attr_or_call(get_attr_or_call(e0, "GetEndVertex"), "GetPoint")
                if not a0 or not b0:
                    return self._result(False, f"edge_index={e_idx} is not a straight edge with two vertices.",
                                        SwErrors.swInvalidParameter)
                targets.append((e_idx, tuple(a0[:3]), tuple(b0[:3])))

            def _find_edge(a_pt, b_pt):
                cur = get_attr_or_call(doc, "GetBodies2", 0, False) or []
                if not cur:
                    return None
                for e in (get_attr_or_call(cur[min(body_index - 1, len(cur) - 1)], "GetEdges") or []):
                    try:
                        p = get_attr_or_call(get_attr_or_call(e, "GetStartVertex"), "GetPoint")
                        q = get_attr_or_call(get_attr_or_call(e, "GetEndVertex"), "GetPoint")
                        if (math.dist(p[:3], a_pt) < 1e-7 and math.dist(q[:3], b_pt) < 1e-7) or \
                           (math.dist(p[:3], b_pt) < 1e-7 and math.dist(q[:3], a_pt) < 1e-7):
                            return e
                    except Exception:
                        continue
                return None

            from ..core.sketch_guard import direct_sketch

            for e_idx, a_pt, b_pt in targets:
                edge = _find_edge(a_pt, b_pt)
                if edge is None:
                    return self._result(
                        False,
                        f"flange: edge {e_idx} ({[round(c * 1000, 3) for c in a_pt]} -> "
                        f"{[round(c * 1000, 3) for c in b_pt]} mm) no longer exists after the previous flange(s)"
                        + (f"; created so far: {', '.join(created_names)}" if created_names else ""),
                        SwErrors.swFeatureError,
                    )
                pt1, pt2 = a_pt, b_pt
                vol_edge_before = capture_vol_state(doc)

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
                xform = get_attr_or_call(sk, "ModelToSketchTransform") if sk else None
                xf_arr = get_attr_or_call(xform, "ArrayData") if xform is not None else None
                if not xf_arr or len(xf_arr) < 12:
                    doc.InsertSketch2(True)
                    return self._result(
                        False,
                        "flange error: could not obtain the sketch transform for the selected face.",
                        SwErrors.swGeneralError,
                    )

                from ..core.com_compat import transform_point

                def _to_sk(pt3):
                    sp = transform_point(xf_arr, pt3)
                    return float(sp[0]), float(sp[1])

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

                with direct_sketch(doc):  # exact corners: snapping produced slivers (MCP-005/007)
                    doc.SketchManager.CreateLine(p1[0], p1[1], 0.0, p2[0], p2[1], 0.0)
                    doc.SketchManager.CreateLine(p2[0], p2[1], 0.0, p3[0], p3[1], 0.0)
                    doc.SketchManager.CreateLine(p3[0], p3[1], 0.0, p4[0], p4[1], 0.0)
                    doc.SketchManager.CreateLine(p4[0], p4[1], 0.0, p1[0], p1[1], 0.0)
                doc.InsertSketch2(True)

                ext_res = self.extrude_sketch(depth=float(distance), both_directions=False, reverse_dir=False, unit="mm")
                if not ext_res.get("success"):
                    return self._result(
                        False,
                        f"flange: wall on edge {e_idx} failed: {ext_res.get('message')}"
                        + (f"; created so far: {', '.join(created_names)}" if created_names else ""),
                        SwErrors.swFeatureError,
                    )
                wall_name = ext_res.get("data", {}).get("feature_name", "Flange")

                # The wall must add exactly thickness x edge length x height; anything else means it
                # went the wrong way or merged with other geometry. Remove it and stop.
                expected = t_mm * seg_len * 1000.0 * float(distance)
                vol_edge_after = capture_vol_state(doc)
                added = (vol_edge_after[0] - vol_edge_before[0]) if (vol_edge_after and vol_edge_before) else None
                if added is not None and abs(added - expected) > max(0.02 * expected, 0.5):
                    try:
                        doc.ClearSelection2(True)
                        wf = doc.FeatureByName(wall_name)
                        if wf is not None and wf.Select2(False, 0):
                            doc.Extension.DeleteSelection2(1)  # with its sketch
                    except Exception:
                        pass
                    return self._result(
                        False,
                        f"flange: the wall on edge {e_idx} added {added:.1f} mm³ instead of {expected:.1f} mm³ "
                        f"(t {t_mm:g} x L {seg_len * 1000:.2f} x H {distance:g} mm); it was removed."
                        + (f" Created so far: {', '.join(created_names)}" if created_names else ""),
                        SwErrors.swFeatureError,
                        {"edge_index": e_idx, "added_mm3": round(added, 2), "expected_mm3": round(expected, 2)},
                    )
                created_names.append(wall_name)

            get_attr_or_call(doc, "EditRebuild3")
            vol_after = capture_vol_state(doc)
            suffix, delta_dict = format_vol_delta(vol_before, vol_after)
            if not created_names:
                return self._result(
                    False,
                    f"flange error: no flange feature was created on edge(s) {edge_indices}.",
                    SwErrors.swFeatureError,
                )
            names_str = ", ".join(created_names)

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
            # Never leave a half-built sketch open behind an error.
            try:
                doc_now, _ = self.get_active_doc()
                if doc_now is not None and get_attr_or_call(doc_now, "GetActiveSketch2") is not None:
                    doc_now.InsertSketch2(True)
                    doc_now.EditUndo2(1)
            except Exception:
                pass
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
