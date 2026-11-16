"""
SolidWorks Feature Operations
-----------------------------
Create 3D features: extrude, cut, fillet, chamfer, etc.

Version: 4.0.0 (Fixed for SolidWorks 2025 - v33)
Author: António Coelho

Fixes v4.0.0:
- FeatureExtrusion2 now uses correct 23 parameters for SW 2025
- Proper sketch close + select before extrude (fixes multi-profile sketches)
- Property vs method fixes (FirstFeature, GetNextFeature, GetTypeName2)
- Added _find_last_sketch helper for reliable sketch selection
- Added _get_sketch_info helper for better error diagnostics
- Better error messages with sketch profile count
"""

import logging
import math
import traceback
from typing import Optional, Dict, Any, List

import win32com.client
import pythoncom

from ..constants import SwErrors, SwEndConditions, SwDeleteSelectionOptions
from ..core.com_compat import get_attr_or_call, null_dispatch, byref_bool
from ..core.post_conditions import capture_pre_mutation_state, verify_mutation_post_condition

logger = logging.getLogger(__name__)


class FeatureOperations:
    """
    Mixin class for feature operations
    
    Requires parent class to have:
    - get_active_doc(): Document access method
    - _result(): Result factory method
    - _units: UnitConverter instance
    """
    
    # ========================================================================
    # Helper Methods
    # ========================================================================
    
    def _recapture_pre_volume(self, doc, pre_state) -> None:
        """Re-read the starting volume after leaving sketch edit (it reads 0 inside a sketch)."""
        try:
            from ..core.post_conditions import capture_vol_state as _cvs
            fresh = _cvs(doc)
            if fresh is not None or getattr(pre_state, "vol_state", None) is None:
                pre_state.vol_state = fresh
        except Exception as _e:
            logger.debug(f"pre-volume recapture failed: {_e}")

    def _find_last_sketch(self, doc) -> Optional[str]:
        """
        Find the name of the last *unused* sketch in the feature tree.
        Sketches already consumed by a feature (they have child features such as
        Boss-Extrude1) are skipped, so a feature call with no new sketch fails
        instead of silently re-using an old profile.

        Returns:
            Sketch name string or None
        """
        last_sketch = None
        try:
            feat = doc.FirstFeature
            while feat is not None:
                try:
                    feat_type = get_attr_or_call(feat, "GetTypeName2")
                    if feat_type == "ProfileFeature":
                        children = None
                        try:
                            children = get_attr_or_call(feat, "GetChildren")
                        except Exception:
                            children = None
                        if not children:
                            last_sketch = feat.Name
                except Exception as e:
                    logger.debug(f"Error checking feature type: {e}")
                try:
                    feat = feat.GetNextFeature
                except Exception:
                    break
        except Exception as e:
            logger.debug(f"_find_last_sketch error: {e}")
        return last_sketch
    
    def _get_sketch_info(self, doc) -> Dict:
        """
        Get diagnostic info about sketches in the document.
        Useful for error messages.
        
        Returns:
            Dict with sketch_count, sketch_names, has_active_sketch
        """
        info = {
            "sketch_count": 0,
            "sketch_names": [],
            "has_active_sketch": False,
            "feature_count": 0
        }
        try:
            # Check if sketch is active (GetActiveSketch2 is non-null only in edit mode)
            try:
                info["has_active_sketch"] = (doc.GetActiveSketch2 is not None)
            except Exception:
                pass
            
            # Count sketches and features
            feat = doc.FirstFeature
            while feat is not None:
                try:
                    feat_type = get_attr_or_call(feat, "GetTypeName2")
                    info["feature_count"] += 1
                    if feat_type == "ProfileFeature":
                        info["sketch_count"] += 1
                        info["sketch_names"].append(feat.Name)
                except Exception:
                    pass
                try:
                    feat = feat.GetNextFeature
                except Exception:
                    break
        except Exception as e:
            logger.debug(f"_get_sketch_info error: {e}")
        return info
    
    def _close_and_select_sketch(self, doc) -> tuple:
        """
        Close active sketch if open (using GetActiveSketch2 check), find and select the last sketch.
        Prevents toggling sketch edit mode when no sketch is active.
        
        Returns:
            Tuple of (success: bool, sketch_name: str, error_msg: str)
        """
        try:
            # Ensure fully constrained sketch requirement is disabled so it doesn't block operations
            try:
                if self._sw_app:
                    self._sw_app.SetUserPreferenceToggle(11, False)
            except Exception:
                pass

            # Step 1: Close active sketch ONLY IF one is actively in edit mode
            is_active = False
            try:
                act = get_attr_or_call(doc, "GetActiveSketch2")
                if act is not None:
                    is_active = True
                else:
                    sk = getattr(doc.SketchManager, "ActiveSketch", None)
                    if sk is not None and getattr(sk, "IsSketchEditable", False):
                        is_active = True
            except Exception:
                pass
            
            if is_active:
                doc.InsertSketch2(True)
                logger.debug("Closed active sketch")
            
            # Step 2: Clear selection
            doc.ClearSelection2(True)
            
            # Step 3: Find the last sketch
            sketch_name = self._find_last_sketch(doc)
            if not sketch_name:
                return False, "", "No sketch found in feature tree"
            
            # Step 4: Select the sketch
            empty_callout = null_dispatch()
            selected = doc.Extension.SelectByID2(
                sketch_name, "SKETCH", 0, 0, 0, False, 0, empty_callout, 0
            )
            
            if not selected:
                return False, sketch_name, f"Could not select sketch '{sketch_name}'"
            
            return True, sketch_name, ""
            
        except Exception as e:
            return False, "", f"Error in sketch selection: {e}"
    
    # ========================================================================
    # Extrude
    # ========================================================================
    
    def extrude_sketch(self, depth: float = 10, both_directions: bool = False,
                       reverse_dir: bool = False, unit: str = None,
                       draft_angle: float = 0.0, draft_outward: bool = False,
                       merge: bool = True) -> Dict:
        """
        Extrude the active sketch (Boss-Extrude)
        FIXED v4.0: Properly closes sketch, selects it, uses 23-param FeatureExtrusion2
        
        Args:
            depth: Extrusion depth
            both_directions: Extrude in both directions (mid-plane)
            reverse_dir: Reverse extrusion direction (extrude backward)
            unit: Unit for depth
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            
            if depth <= 0:
                return self._result(
                    False,
                    f"Invalid extrusion depth {depth}: depth must be greater than 0.",
                    SwErrors.swInvalidParameter,
                )

            # Convert depth to meters
            depth_m = self._units.to_meters(depth, unit)
            unit_str = unit or self._units.default_unit.value
            
            pre_state = capture_pre_mutation_state(doc)
            
            # Step 1: Close sketch and select it
            success, sketch_name, error_msg = self._close_and_select_sketch(doc)
            if not success:
                sketch_info = self._get_sketch_info(doc)
                return self._result(False,
                    f"Extrusion failed: {error_msg}. "
                    f"Sketches found: {sketch_info['sketch_count']} {sketch_info['sketch_names']}. "
                    f"Active sketch: {sketch_info['has_active_sketch']}. "
                    f"Try: create_sketch → draw geometry → extrude_sketch",
                    SwErrors.swFeatureError,
                    {"diagnostics": sketch_info})

            self._recapture_pre_volume(doc, pre_state)

            # Step 2: Determine end condition
            end_cond = 6 if both_directions else 0  # 6=MidPlane, 0=Blind
            draft_on = abs(float(draft_angle or 0.0)) > 1e-9
            draft_rad = math.radians(abs(float(draft_angle or 0.0)))
            if draft_on and not (0.0 < abs(float(draft_angle)) < 89.0):
                return self._result(False, "draft_angle must be between 0 and 89 degrees.", SwErrors.swInvalidParameter)
            
            # Step 3: Try extrusion methods
            feat = None
            method_used = ""
            
            # Method 1: FeatureExtrusion2 with 23 params (SW 2025 / v33)
            try:
                feat = doc.FeatureManager.FeatureExtrusion2(
                    True,           # Sd - single direction
                    False,          # Flip
                    bool(reverse_dir),  # Dir - direction (True = reverse)
                    end_cond,       # T1 - end condition (0=Blind, 6=MidPlane)
                    0,              # T2 - end condition 2
                    depth_m,        # D1 - depth
                    depth_m,        # D2 - depth 2
                    draft_on,       # Dchk1 - draft on/off
                    draft_on,       # Dchk2 - draft on/off 2
                    bool(draft_outward),  # Ddir1 - draft outward
                    bool(draft_outward),  # Ddir2 - draft outward 2
                    draft_rad,      # Dang1 - draft angle (radians)
                    draft_rad,      # Dang2 - draft angle 2
                    False,          # OffsetReverse1
                    False,          # OffsetReverse2
                    False,          # TranslateSurface1
                    False,          # TranslateSurface2
                    bool(merge),    # Merge - merge result
                    True,           # UseFeatScope
                    True,           # UseAutoSelect
                    0,              # T0 - start condition
                    0.0,            # StartOffset
                    False           # FlipStartOffset
                )
                if feat:
                    method_used = "FeatureExtrusion2_23p"
            except Exception as e:
                logger.debug(f"FeatureExtrusion2 (23p) failed: {e}")
            
            # Method 2: FeatureExtrusion3 with 23 params
            if feat is None:
                try:
                    feat = doc.FeatureManager.FeatureExtrusion3(
                        True, False, bool(reverse_dir), end_cond, 0,
                        depth_m, depth_m,
                        draft_on, draft_on, bool(draft_outward), bool(draft_outward),
                        draft_rad, draft_rad,
                        False, False, False, False,
                        bool(merge), True, True,
                        0, 0.0, False
                    )
                    if feat:
                        method_used = "FeatureExtrusion3"
                except Exception as e:
                    logger.debug(f"FeatureExtrusion3 failed: {e}")
            
            if feat is None:
                sketch_info = self._get_sketch_info(doc)
                return self._result(False,
                    f"Extrusion failed on sketch '{sketch_name}'. "
                    f"Ensure sketch has a closed profile (circle, rectangle, etc). "
                    f"Sketches in model: {sketch_info['sketch_names']}",
                    SwErrors.swFeatureError,
                    {"sketch_name": sketch_name, "diagnostics": sketch_info})
            
            # Post-condition verification (F3)
            ok, post_msg, post_diag = verify_mutation_post_condition(doc, feat, pre_state, "feature")
            if not ok:
                return self._result(False, f"Extrusion failed post-condition verification: {post_msg}",
                                  SwErrors.swFeatureError, post_diag)
            
            direction = "both directions (mid-plane)" if both_directions else "one direction"
            
            return self._result(True,
                f"Extruded {depth}{unit_str} ({direction}) [{method_used}]",
                SwErrors.swSuccess,
                {"depth": depth, "unit": unit_str,
                 "both_directions": both_directions,
                 "sketch_name": sketch_name,
                 "api_method": method_used,
                 "feature_name": getattr(feat, "Name", "Boss-Extrude")})
            
        except Exception as e:
            logger.error(f"Extrude error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swFeatureError)
    
    # ========================================================================
    # Cut Extrude
    # ========================================================================
    
    def cut_extrude(self, depth: float = 10, through_all: bool = False,
                    both_directions: bool = False, unit: str = None,
                    reverse_dir: Optional[bool] = None) -> Dict:
        """
        Cut extrude (remove material)
        FIXED v4.0: Proper sketch handling and parameter counts

        Args:
            depth: Cut depth (ignored if through_all=True). With both_directions it is the TOTAL
                   depth, split equally either side of the sketch (mid-plane).
            through_all: Cut through entire model
            both_directions: Mid-plane cut (or through-all on both sides)
            unit: Unit for depth
            reverse_dir: True/False forces the side of a one-sided cut (False = against the sketch
                         normal, i.e. into the face for a face sketch; True = along the normal, verified
                         live); None tries the default side first and the opposite side if that fails
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            
            if not through_all and depth <= 0:
                return self._result(
                    False,
                    f"Invalid cut depth {depth}: depth must be greater than 0.",
                    SwErrors.swInvalidParameter,
                )

            # Convert depth to meters
            depth_m = self._units.to_meters(depth, unit)
            unit_str = unit or self._units.default_unit.value
            
            pre_state = capture_pre_mutation_state(doc)

            # Step 1: Close sketch and select it
            success, sketch_name, error_msg = self._close_and_select_sketch(doc)
            if not success:
                sketch_info = self._get_sketch_info(doc)
                return self._result(False,
                    f"Cut failed: {error_msg}. "
                    f"Sketches: {sketch_info['sketch_names']}. "
                    f"Ensure sketch is on an existing face.",
                    SwErrors.swFeatureError,
                    {"diagnostics": sketch_info})

            # Mass properties read as 0 while a sketch is being edited; retake the
            # starting volume now that the sketch is closed.
            self._recapture_pre_volume(doc, pre_state)

            # Determine end condition (values from swEndConditions_e in the interop DLL)
            if through_all:
                if both_directions:
                    end_cond = 9  # swEndCondThroughAllBoth (2 is ThroughNext: it stopped at the next face, MCP-009)
                else:
                    end_cond = 1  # swEndCondThroughAll
                cut_depth = 0
            else:
                if both_directions:
                    end_cond = 6  # swEndCondMidPlane: `depth` is the TOTAL, split equally either side
                else:
                    end_cond = 0  # swEndCondBlind
                cut_depth = depth_m

            # Through-all on both sides is built as a true two-direction cut (direction 1 and
            # direction 2 both "through all"). The single-direction ThroughAllBoth end condition cut
            # only one side on some calls (MCP-012/025).
            two_sided = bool(through_all and both_directions)

            feat = None
            method_used = ""
            # reverse_dir None = try the default side, then the opposite one (old behaviour);
            # True/False = only that side, so the caller controls which way a one-sided cut goes.
            if reverse_dir is None or both_directions:
                dir_options = [False, True]
            else:
                dir_options = [bool(reverse_dir)]

            # Method 1: FeatureCut4 (27 params for SW 2025)
            for dir_opt in dir_options:
                try:
                    feat = doc.FeatureManager.FeatureCut4(
                        not two_sided,  # Sd - single direction
                        False,          # Flip
                        dir_opt,        # Dir - direction (try normal, then opposite)
                        1 if two_sided else end_cond,  # T1 - end condition
                        1 if two_sided else 0,         # T2 - end condition 2
                        cut_depth,      # D1 - depth
                        0,              # D2 - depth 2
                        False, False,   # Dchk1, Dchk2 - draft on/off
                        False, False,   # Ddir1, Ddir2 - draft direction
                        0.0, 0.0,       # Dang1, Dang2 - draft angle
                        False, False,   # OffsetReverse1, OffsetReverse2
                        False, False,   # TranslateSurface1, TranslateSurface2
                        False,          # NormalCut
                        True,           # UseFeatScope
                        True,           # UseAutoSelect
                        False,          # AssemblyFeatureScope
                        True,           # AutoSelectComponents
                        False,          # PropagateFeatureToParts
                        0,              # T0 - start condition
                        0.0,            # StartOffset
                        False,          # FlipStartOffset
                        False           # OptimizeGeometry
                    )
                    if feat:
                        method_used = "FeatureCut4" if not dir_opt else "FeatureCut4_Rev"
                        break
                except Exception as e:
                    logger.debug(f"FeatureCut4 (dir={dir_opt}) failed: {e}")
            
            # Method 2: FeatureCut3 (26 params fallback)
            if feat is None:
                for dir_opt in dir_options:
                    try:
                        feat = doc.FeatureManager.FeatureCut3(
                            True, False, dir_opt, end_cond, 0,
                            cut_depth, 0,
                            False, False, False, False,
                            0.0, 0.0,
                            False, False, False, False,
                            False, True, True,
                            False, True, False,
                            0, 0.0, False
                        )
                        if feat:
                            method_used = "FeatureCut3" if not dir_opt else "FeatureCut3_Rev"
                            break
                    except Exception as e:
                        logger.debug(f"FeatureCut3 (dir={dir_opt}) failed: {e}")
            
            if feat is None:
                side_hint = ""
                if reverse_dir is not None and not both_directions:
                    side_hint = (f" With reverse_dir={bool(reverse_dir)} the cut goes "
                                 f"{'along' if reverse_dir else 'against'} the sketch normal; there may be no material "
                                 f"on that side - try reverse_dir={not bool(reverse_dir)} or omit it.")
                return self._result(False,
                    f"Cut failed on sketch '{sketch_name}'. "
                    f"Ensure sketch is drawn on an existing face with a closed profile.{side_hint}",
                    SwErrors.swFeatureError,
                    {"sketch_name": sketch_name})
            
            # Post-condition verification (F3)
            ok, post_msg, post_diag = verify_mutation_post_condition(doc, feat, pre_state, "feature")
            if not ok:
                return self._result(False, f"Cut extrude failed post-condition verification: {post_msg}",
                                  SwErrors.swFeatureError, post_diag)
            
            if through_all:
                cut_type = "through all, both directions" if both_directions else "through all"
            elif both_directions:
                cut_type = f"mid-plane, {depth}{unit_str} total ({depth / 2:g}{unit_str} each side)"
            else:
                cut_type = f"{depth}{unit_str}"
            reversed_used = method_used.endswith("_Rev")

            return self._result(True, f"Cut extrude: {cut_type}" + (", reversed direction" if reversed_used and not both_directions else "")
                              + f" [{method_used}]",
                              SwErrors.swSuccess,
                              {"depth": depth, "through_all": through_all,
                               "both_directions": both_directions,
                               "end_condition": {0: "blind", 1: "through_all", 6: "mid_plane", 9: "through_all_both"}[end_cond],
                               "reversed_direction": reversed_used,
                               "sketch_name": sketch_name,
                               "api_method": method_used,
                               "feature_name": getattr(feat, "Name", "Cut-Extrude")})
            
        except Exception as e:
            logger.error(f"Cut extrude error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swFeatureError)
    
    # ========================================================================
    # Fillet
    # ========================================================================
    
    def fillet_edges(self, radius: float = 2, unit: str = None) -> Dict:
        """
        Add fillet to selected edges
        
        Args:
            radius: Fillet radius
            unit: Unit for radius
        
        Returns:
            Result dictionary
        
        Note: Select edges first using execute_python or manual selection
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            
            # Check selection count - fail immediately if no edges selected
            sel_mgr = doc.SelectionManager
            sel_count = sel_mgr.GetSelectedObjectCount2(-1)
            if sel_count == 0:
                return self._result(False, "No edges selected. Select edges before calling fillet_edges.",
                                  SwErrors.swSelectionError)
            
            if radius <= 0:
                return self._result(
                    False,
                    f"Invalid fillet radius {radius}: radius must be greater than 0.",
                    SwErrors.swInvalidParameter,
                )

            radius_m = self._units.to_meters(radius, unit)
            unit_str = unit or self._units.default_unit.value
            pre_state = capture_pre_mutation_state(doc)
            
            feat = None
            method_used = ""
            
            # Method 1: FeatureManager.FeatureFillet3 (returns IFeature object)
            try:
                feat = doc.FeatureManager.FeatureFillet3(
                    195, radius_m, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
                )
                if feat:
                    method_used = "FeatureManager.FeatureFillet3"
            except Exception as e:
                logger.debug(f"FeatureManager.FeatureFillet3 failed: {e}")

            # Method 2: FeatureManager.FeatureFillet (7 params)
            if feat is None:
                try:
                    feat = doc.FeatureManager.FeatureFillet(
                        195, radius_m, 0, 0, 0, 0, 0
                    )
                    if feat:
                        method_used = "FeatureManager.FeatureFillet"
                except Exception as e:
                    logger.debug(f"FeatureManager.FeatureFillet failed: {e}")

            # Method 3: doc.FeatureFillet3 (0 is swFeatureFilletError_NoError; non-zero is failure!)
            if feat is None:
                null_obj = win32com.client.VARIANT(win32com.client.pythoncom.VT_EMPTY, None)
                try:
                    ret = doc.FeatureFillet3(radius_m, True, 0, False, 0, 0, null_obj, False, False)
                    if ret == 0:
                        method_used = "doc.FeatureFillet3"
                        feat = True
                    else:
                        logger.debug(f"doc.FeatureFillet3 returned error code: {ret}")
                except Exception as e:
                    logger.debug(f"doc.FeatureFillet3 failed: {e}")
            
            if feat is None or feat is False:
                return self._result(False,
                    f"Fillet failed: radius {radius}{unit_str} cannot be created on selected geometry.",
                    SwErrors.swFeatureError)
            
            # Post-condition verification (F3)
            ok, post_msg, post_diag = verify_mutation_post_condition(doc, feat, pre_state, "feature")
            if not ok:
                return self._result(False, f"Fillet failed post-condition verification: {post_msg}",
                                  SwErrors.swFeatureError, post_diag)
            
            return self._result(True, f"Fillet: r={radius}{unit_str} [{method_used}]",
                              SwErrors.swSuccess,
                              {"radius": radius, "unit": unit_str,
                               "feature_name": getattr(feat, "Name", "Fillet") if hasattr(feat, "Name") else "Fillet"})
            
        except Exception as e:
            logger.error(f"Fillet error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swFeatureError)
    
    # ========================================================================
    # Chamfer
    # ========================================================================
    
    def chamfer_edges(self, distance: float = 2, angle: float = 45,
                      unit: str = None) -> Dict:
        """
        Add chamfer to selected edges
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            
            # Check selection count - fail immediately if no edges selected
            sel_mgr = doc.SelectionManager
            sel_count = sel_mgr.GetSelectedObjectCount2(-1)
            if sel_count == 0:
                return self._result(False, "No edges selected. Select edges before calling chamfer_edges.",
                                  SwErrors.swSelectionError)
            
            if distance <= 0:
                return self._result(
                    False,
                    f"Invalid chamfer distance {distance}: distance must be greater than 0.",
                    SwErrors.swInvalidParameter,
                )

            import math
            dist_m = self._units.to_meters(distance, unit)
            angle_rad = math.radians(angle)
            unit_str = unit or self._units.default_unit.value
            pre_state = capture_pre_mutation_state(doc)
            
            feat = None
            
            try:
                feat = doc.FeatureManager.InsertFeatureChamfer(
                    0, 1, dist_m, angle_rad, 0.0, 0.0, 0.0, 0.0
                )
            except Exception as e:
                logger.debug(f"InsertFeatureChamfer failed: {e}")
            
            if feat is None:
                return self._result(False,
                    f"Chamfer failed: distance {distance}{unit_str} cannot be created on selected geometry.",
                    SwErrors.swFeatureError)
            
            # Post-condition verification (F3)
            ok, post_msg, post_diag = verify_mutation_post_condition(doc, feat, pre_state, "feature")
            if not ok:
                return self._result(False, f"Chamfer failed post-condition verification: {post_msg}",
                                  SwErrors.swFeatureError, post_diag)
            
            return self._result(True, f"Chamfer: {distance}{unit_str} x {angle}\u00b0",
                              SwErrors.swSuccess,
                              {"distance": distance, "angle": angle, "unit": unit_str,
                               "feature_name": getattr(feat, "Name", "Chamfer") if hasattr(feat, "Name") else "Chamfer"})
            
        except Exception as e:
            logger.error(f"Chamfer error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swFeatureError)
    
    # ========================================================================
    # List Features (FIXED: properties not methods)
    # ========================================================================
    
    def list_features(self) -> Dict:
        """
        List all features in the active document
        FIXED v4.0: Uses properties (FirstFeature, GetNextFeature, GetTypeName2)
        instead of method calls for SW 2025 compatibility.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            
            features = []
            
            # FIXED: Use property access, not method calls
            feat = doc.FirstFeature
            
            while feat is not None:
                try:
                    name = feat.Name
                    # FIXED: GetTypeName2 is a property in SW 2025
                    feat_type = feat.GetTypeName2
                    
                    features.append({
                        "name": name,
                        "type": feat_type,
                    })

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
                                features.append({
                                    "name": s_name,
                                    "type": str(s_type),
                                    "parent": name,
                                })
                            except Exception:
                                pass
                            next_sub = getattr(subfeat, "GetNextSubFeature", None)
                            subfeat = next_sub() if callable(next_sub) else next_sub
                    except Exception:
                        pass
                except Exception:
                    pass
                
                # FIXED: GetNextFeature is a property in SW 2025
                try:
                    feat = feat.GetNextFeature
                except Exception:
                    break
            
            return self._result(True, f"{len(features)} features found",
                              SwErrors.swSuccess,
                              {"features": features, "count": len(features)})
            
        except Exception as e:
            logger.error(f"List features error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swGeneralError)
    
    # ========================================================================
    # Edge Selection Helper
    # ========================================================================
    
    def select_edge(self, edge_index: int = 1) -> Dict:
        """
        Select an edge by index
        Note: Use execute_python for precise edge selection
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            
            doc.ClearSelection2(True)
            
            return self._result(True, "Use execute_python for edge selection",
                              SwErrors.swSuccess)
            
        except Exception as e:
            return self._result(False, f"Error: {e}", SwErrors.swSelectionError)

    # ========================================================================
    # Dimension Operations (Tool 1)
    # ========================================================================

    DIMENSION_TYPE_MAP = {
        0: "Unknown",
        1: "Ordinate",
        2: "Linear",
        3: "Angular",
        4: "ArcLength",
        5: "Radial",
        6: "Diameter",
        7: "HorOrdinate",
        8: "VertOrdinate",
        9: "ZAxis",
        10: "Chamfer",
        11: "HorLinear",
        12: "VertLinear",
        13: "Scalar",
        14: "RadialLinear",
        15: "DiametricLinear",
        16: "AngularOrdinate",
    }

    def get_dimensions(
        self,
        feature_name: Optional[str] = None,
        dimension_name: Optional[str] = None
    ) -> Dict:
        """
        List dimensions from active model document.
        Returns full name (D1@Sketch1), value in document units, owner feature, and type.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            import math
            unit_str = self._units.default_unit.value if hasattr(self, "_units") else "mm"

            collected_dims = []
            visited_names = set()

            def process_dim(dim_obj, owner_feat_name, disp_dim_obj=None):
                if not dim_obj:
                    return
                try:
                    full_name = getattr(dim_obj, "FullName", "")
                    if not full_name or full_name in visited_names:
                        return
                    visited_names.add(full_name)

                    dim_type_code = 0
                    if disp_dim_obj:
                        try:
                            dim_type_code = get_attr_or_call(disp_dim_obj, "GetType2")
                            if dim_type_code is None:
                                dim_type_code = 0
                        except Exception:
                            dim_type_code = 0

                    if not dim_type_code:
                        t = getattr(dim_obj, "GetType", 0)
                        if callable(t):
                            try:
                                t = t()
                            except Exception:
                                t = 0
                        # Map IDimension.GetType() swDimensionParamType_e: 0=Linear, 1=Angular
                        if t == 0:
                            dim_type_code = 2
                        elif t == 1:
                            dim_type_code = 3
                        else:
                            dim_type_code = int(t)

                    dim_type_name = self.DIMENSION_TYPE_MAP.get(int(dim_type_code), f"Type_{dim_type_code}")

                    sys_val = getattr(dim_obj, "SystemValue", 0.0)

                    # IDimension.GetType: swDimensionParamType_e 2 = integer (e.g. pattern instance count)
                    param_type = None
                    try:
                        param_type = get_attr_or_call(dim_obj, "GetType")
                    except Exception:
                        param_type = None

                    # Value in document units
                    if param_type == 2:
                        val_doc = float(int(round(float(sys_val))))
                        unit_label = "count"
                        dim_type_name = "Integer"
                    elif dim_type_code in (1, 3, 16):  # Angular
                        val_doc = float(sys_val) * 180.0 / math.pi
                        unit_label = "deg"
                    else:  # Linear
                        val_doc = self._units.from_meters(float(sys_val), unit_str)
                        unit_label = unit_str

                    name_short = getattr(dim_obj, "Name", "")

                    item = {
                        "full_name": full_name,
                        "name": name_short,
                        "owner_feature": owner_feat_name,
                        "type": dim_type_name,
                        "type_code": int(dim_type_code),
                        "system_value": float(sys_val),
                        "value": round(float(val_doc), 6),
                        "units": unit_label,
                    }

                    if feature_name:
                        fn_low = feature_name.strip().lower()
                        if fn_low not in owner_feat_name.lower() and fn_low not in full_name.lower():
                            return

                    if dimension_name:
                        dn_low = dimension_name.strip().lower()
                        if dn_low != name_short.lower() and dn_low not in full_name.lower():
                            return

                    collected_dims.append(item)
                except Exception as ex:
                    logger.debug(f"Error processing dimension: {ex}")

            def inspect_feature_dimensions(f):
                if not f:
                    return
                f_name = getattr(f, "Name", "")
                disp_dim = get_attr_or_call(f, "GetFirstDisplayDimension")
                while disp_dim is not None:
                    try:
                        d = get_attr_or_call(disp_dim, "GetDimension")
                        process_dim(d, f_name, disp_dim)
                    except Exception:
                        pass
                    try:
                        disp_dim = f.GetNextDisplayDimension(disp_dim)
                    except Exception:
                        break

            # 1. Traverse all features in FeatureManager tree
            feat = doc.FirstFeature
            while feat is not None:
                inspect_feature_dimensions(feat)
                # Check sub-features (absorbed sketches, etc.)
                try:
                    sub = get_attr_or_call(feat, "GetFirstSubFeature")
                    while sub is not None:
                        inspect_feature_dimensions(sub)
                        sub = sub.GetNextSubFeature
                except Exception:
                    pass

                try:
                    feat = feat.GetNextFeature
                except Exception:
                    break

            return self._result(
                True,
                f"Found {len(collected_dims)} dimension(s)",
                SwErrors.swSuccess,
                {"dimensions": collected_dims, "count": len(collected_dims)}
            )
        except Exception as e:
            logger.error(f"get_dimensions error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error getting dimensions: {e}", SwErrors.swGeneralError)

    def set_dimension(
        self,
        name: str,
        value: float,
        units: Optional[str] = None,
        config_scope: str = "this_configuration"
    ) -> Dict:
        """
        Set a dimension's value using SystemValue conventions (meters for linear, radians for angular).
        Sets configuration scope explicitly via swSetValueInConfiguration_e (default: single configuration).
        Rebuilds document, checks for failing features, and rolls back on failure.
        Post-conditions: verifies value read-back and volume/bbox change.
        """
        try:
            import math

            # Proactive input validation (Zero/Negative/NaN/Inf) BEFORE ANY COM CALL
            try:
                target_num = float(value)
                if math.isnan(target_num) or math.isinf(target_num):
                    raise ValueError("NaN or Inf")
            except (ValueError, TypeError):
                return self._result(
                    False,
                    f"Invalid dimension value '{value}': must be numeric and finite.",
                    SwErrors.swInvalidInput
                )

            units_clean = str(units).strip().lower() if units else ""
            is_explicitly_angular = units_clean in ("deg", "degree", "degrees", "rad", "radian", "radians")

            # For linear/radial/diameter dimensions, non-positive values (<= 0) are geometrically invalid.
            # Reject immediately BEFORE any COM call when units indicate linear or when units is None (model default unit MMGS).
            # Angular dimensions permit negative values (representing offset direction / reversed angle) and 0.
            if not is_explicitly_angular and target_num <= 0:
                return self._result(
                    False,
                    f"Invalid dimension value {target_num}: linear, radial, and diameter dimensions must be strictly positive (> 0).",
                    SwErrors.swInvalidInput
                )

            doc, err = self.get_active_doc()
            if err:
                return err

            # Configuration scope mapping (swSetValueInConfiguration_e)
            scope_map = {
                "this_configuration": 1,      # swSetValue_InThisConfiguration
                "single": 1,
                "all_configurations": 2,       # swSetValue_InAllConfigurations
                "all": 2,
                "specific_configurations": 3,  # swSetValue_InSpecificConfigurations
                "specific": 3,
                "use_current": 0,              # swSetValue_UseCurrentSetting
            }
            config_scope_clean = str(config_scope).strip().lower()
            if config_scope_clean not in scope_map:
                return self._result(
                    False,
                    f"Invalid config_scope '{config_scope}'. Allowed: 'this_configuration', 'all_configurations', 'specific_configurations'.",
                    SwErrors.swInvalidParameter
                )
            config_scope_val = scope_map[config_scope_clean]

            # Check if owning feature is suppressed
            feat_part = None
            if "@" in name:
                parts = name.split("@")
                if len(parts) >= 2:
                    feat_part = parts[1]

            if feat_part:
                feat = None
                try:
                    feat = doc.FeatureByName(feat_part)
                except Exception:
                    pass
                if not feat:
                    f_cur = doc.FirstFeature
                    while f_cur is not None:
                        if getattr(f_cur, "Name", "").lower() == feat_part.lower():
                            feat = f_cur
                            break
                        try:
                            sub = get_attr_or_call(f_cur, "GetFirstSubFeature")
                            while sub is not None:
                                if getattr(sub, "Name", "").lower() == feat_part.lower():
                                    feat = sub
                                    break
                                sub = sub.GetNextSubFeature
                            if feat:
                                break
                        except Exception:
                            pass
                        try:
                            f_cur = f_cur.GetNextFeature
                        except Exception:
                            break

                if feat:
                    try:
                        is_supp = feat.IsSuppressed() if callable(getattr(feat, "IsSuppressed", None)) else feat.IsSuppressed
                        if is_supp:
                            return self._result(
                                False,
                                f"Cannot modify dimension '{name}': Owning feature '{feat_part}' is suppressed.",
                                SwErrors.swInvalidParameter,
                                {"feature": feat_part, "suppressed": True}
                            )
                    except Exception:
                        pass

            # 1. Resolve IDimension object
            dim = None
            try:
                dim = doc.Parameter(name)
                if not dim and "@" in name:
                    parts = name.split("@")
                    candidates = [f"{parts[0]}@{parts[1]}"]
                    if len(parts) >= 3:
                        candidates.append(f"{parts[0]}@{parts[1]}@{parts[2].split('.')[0]}")
                    for cand in candidates:
                        try:
                            dim = doc.Parameter(cand)
                            if dim:
                                break
                        except Exception:
                            pass
            except Exception:
                pass

            if not dim:
                return self._result(
                    False,
                    f"Dimension '{name}' not found in active document.",
                    SwErrors.swDimensionNotFound,
                    {"requested_name": name}
                )

            # Check if dimension is accessible and retrieve old value
            try:
                old_sys_val = float(dim.SystemValue)
            except Exception as e:
                return self._result(
                    False,
                    f"Cannot access dimension '{name}': {e}. The owning feature may be suppressed.",
                    SwErrors.swInvalidParameter
                )

            # Check if dimension is controlled by an equation in EquationManager
            try:
                eq_mgr = getattr(doc, "GetEquationMgr", None)
                if eq_mgr is not None:
                    eq_count = get_attr_or_call(eq_mgr, "GetCount")
                    for eq_idx in range(eq_count):
                        try:
                            eq_str = eq_mgr.Equation(eq_idx)
                            if callable(eq_str):
                                eq_str = eq_str()
                            if eq_str and "=" in eq_str:
                                lhs, _ = eq_str.split("=", 1)
                                lhs_clean = lhs.replace('"', '').replace("'", "").strip().lower()
                                dim_full = getattr(dim, "FullName", name).strip().lower()
                                dim_req = name.strip().lower()
                                if dim_req == lhs_clean or dim_full == lhs_clean or (dim_full in lhs_clean) or (dim_req in lhs_clean):
                                    return self._result(
                                        False,
                                        f"Cannot modify dimension '{name}': controlled by equation '{eq_str.strip()}'; edit the equation or global variable.",
                                        SwErrors.swInvalidParameter,
                                        {"dimension": name, "equation": eq_str.strip(), "equation_index": eq_idx}
                                    )
                        except Exception:
                            pass
            except Exception as eq_ex:
                logger.debug(f"Equation check error: {eq_ex}")

            # Check if dimension is driven or reference via verified COM type library properties:
            # - IDimension.DrivenState (swDimensionDrivenState_e: 1=swDimensionDriven, 2=swDimensionDriving)
            # - IDimension.ReadOnly (bool)
            # - IDimension.IsReference (bool)
            driven_state = get_attr_or_call(dim, "DrivenState")
            is_ref = get_attr_or_call(dim, "IsReference")
            is_ro = get_attr_or_call(dim, "ReadOnly")
            if driven_state == 1 or is_ref or is_ro:
                reason = "swDimensionDriven (state 1)" if driven_state == 1 else ("reference" if is_ref else "read-only")
                return self._result(
                    False,
                    f"Cannot modify dimension '{name}': dimension is driven/reference ({reason}).",
                    SwErrors.swInvalidParameter,
                    {"dimension": name, "driven_state": driven_state, "is_reference": is_ref, "read_only": is_ro}
                )

            dim_type_code = getattr(dim, "GetType", 0)
            if callable(dim_type_code):
                try:
                    dim_type_code = dim_type_code()
                except Exception:
                    dim_type_code = 0
            is_angular = (dim_type_code in (1, 3, 16))

            # 2. Convert value to SystemValue (meters for linear, radians for angular)
            if is_angular:
                units_clean = (units or "deg").strip().lower()
                if units_clean in ("deg", "degree", "degrees"):
                    new_sys_val = target_num * math.pi / 180.0
                elif units_clean in ("rad", "radian", "radians"):
                    new_sys_val = target_num
                else:
                    return self._result(
                        False,
                        f"Invalid units '{units}' for angular dimension '{name}'. Use 'deg' or 'rad'.",
                        SwErrors.swInvalidParameter
                    )
            else:
                # Linear dimension
                if units:
                    units_clean = units.strip().lower()
                    if units_clean in ("mm", "millimeter", "millimeters"):
                        new_sys_val = target_num * 0.001
                    elif units_clean in ("in", "inch", "inches"):
                        new_sys_val = target_num * 0.0254
                    elif units_clean in ("m", "meter", "meters"):
                        new_sys_val = target_num
                    elif units_clean in ("cm", "centimeter", "centimeters"):
                        new_sys_val = target_num * 0.01
                    else:
                        return self._result(
                            False,
                            f"Invalid units '{units}' for linear dimension '{name}'. Use 'mm', 'in', 'm', or 'cm'.",
                            SwErrors.swInvalidParameter
                        )
                else:
                    doc_unit_str = self._units.default_unit.value if hasattr(self, "_units") else "mm"
                    new_sys_val = self._units.to_meters(target_num, doc_unit_str)

            # Capture pre-mutation state for post-condition & rollback
            pre_state = capture_pre_mutation_state(doc)
            pre_insp = self.inspect_model()

            # 3. Apply new value via SetSystemValue3
            if config_scope_val == 1:
                act_cfg = getattr(doc.ConfigurationManager, "ActiveConfiguration", None)
                act_name = getattr(act_cfg, "Name", "") if act_cfg else ""
                set_ret = dim.SetSystemValue3(new_sys_val, 3, act_name) if act_name else dim.SetSystemValue3(new_sys_val, 1, "")
            else:
                set_ret = dim.SetSystemValue3(new_sys_val, config_scope_val, "")
            ret_code = int(set_ret) if set_ret is not None else 0
            if ret_code != 0:
                ret_names = {
                    1: "swSetValue_Failure",
                    2: "swSetValue_InvalidValue",
                    3: "swSetValue_DrivenDimension",
                    4: "swSetValue_ModelNotLoaded",
                    5: "swSetValue_FrozenFeatureOwner",
                }
                ret_desc = ret_names.get(ret_code, f"ErrorCode_{ret_code}")
                return self._result(
                    False,
                    f"SetSystemValue3 failed for '{name}' with status {ret_code} ({ret_desc}).",
                    SwErrors.swFeatureError,
                    {"return_code": ret_code, "status": ret_desc}
                )

            # 4. Rebuild document
            get_attr_or_call(doc, "EditRebuild3")

            # 5. Check rebuild errors & failing features
            ok_post, post_msg, post_diag = verify_mutation_post_condition(doc, None, pre_state, "general")
            if not ok_post:
                logger.warning(f"Rebuild failed after setting dimension '{name}': {post_msg}. Rolling back to {old_sys_val}...")
                dim.SetSystemValue3(old_sys_val, config_scope_val, "")
                get_attr_or_call(doc, "EditRebuild3")
                return self._result(
                    False,
                    f"Rebuild failed after setting dimension '{name}': {post_msg}. Rolled back to original value {old_sys_val}.",
                    SwErrors.swRebuildError,
                    {"post_condition": post_diag, "rolled_back": True}
                )

            # 6. Post-condition: Read back the value and verify match
            read_back_sys = float(dim.SystemValue)
            if abs(read_back_sys - new_sys_val) > 1e-6:
                dim.SetSystemValue3(old_sys_val, config_scope_val, "")
                get_attr_or_call(doc, "EditRebuild3")
                return self._result(
                    False,
                    f"Dimension post-condition failed: read-back value {read_back_sys} does not match target {new_sys_val}. Rolled back.",
                    SwErrors.swFeatureError,
                    {"read_back_system_value": read_back_sys, "target_system_value": new_sys_val}
                )

            # Post-condition: check volume / bbox change
            post_insp = self.inspect_model()
            geom_changed = False
            pre_dims = pre_insp.get("data", {}).get("dimensions_mm", {})
            post_dims = post_insp.get("data", {}).get("dimensions_mm", {})
            if pre_dims and post_dims:
                geom_changed = any(abs(post_dims.get(k, 0) - pre_dims.get(k, 0)) > 1e-4 for k in ("dx", "dy", "dz"))
            # Also count volume / face / edge changes: an internal hole or fillet can change
            # without touching the bounding box.
            pre_d, post_d = pre_insp.get("data", {}) or {}, post_insp.get("data", {}) or {}
            if not geom_changed:
                geom_changed = (
                    abs(float(post_d.get("volume_mm3", 0) or 0) - float(pre_d.get("volume_mm3", 0) or 0)) > 1e-3
                    or post_d.get("faces_count") != pre_d.get("faces_count")
                    or post_d.get("edges_count") != pre_d.get("edges_count")
                )

            full_dim_name = getattr(dim, "FullName", name)

            return self._result(
                True,
                f"Successfully set dimension '{full_dim_name}' to {value} {units or 'doc units'} (SystemValue: {new_sys_val:.6g}). Rebuild verified.",
                SwErrors.swSuccess,
                {
                    "name": full_dim_name,
                    "target_value": value,
                    "units": units,
                    "system_value": round(new_sys_val, 8),
                    "previous_system_value": round(old_sys_val, 8),
                    "read_back_system_value": round(read_back_sys, 8),
                    "geometry_changed": geom_changed,
                    "bounding_box_pre": pre_dims,
                    "bounding_box_post": post_dims,
                }
            )
        except Exception as e:
            logger.error(f"set_dimension error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error setting dimension: {e}", SwErrors.swGeneralError)

    _REF_GEOM_TYPES = {"RefPlane", "RefAxis", "RefPoint", "CoordSys", "OriginProfileFeature"}
    _SKETCH_TYPES = {"ProfileFeature", "3DProfileFeature"}

    def _set_feature_visibility(self, doc, feat, name: str, hide: bool) -> Dict:
        """Hide/show reference geometry, a sketch, or the solid bodies a feature produced."""
        verb = "hidden" if hide else "shown"
        type_name = str(get_attr_or_call(feat, "GetTypeName2") or "")
        doc.ClearSelection2(True)
        if type_name in self._REF_GEOM_TYPES or type_name in self._SKETCH_TYPES:
            if not feat.Select2(False, 0):
                return self._result(False, f"Could not select '{name}'", SwErrors.swSelectionError)
            if type_name in self._SKETCH_TYPES:
                get_attr_or_call(doc, "BlankSketch" if hide else "UnblankSketch")
            else:
                get_attr_or_call(doc, "BlankRefGeom" if hide else "UnblankRefGeom")
            doc.ClearSelection2(True)
            try:
                vis = get_attr_or_call(feat, "Visible")  # swVisibilityState_e: 1 = hidden, 2 = shown
            except Exception:
                vis = None
            if vis in (1, 2) and (vis == 1) != hide:
                return self._result(False, f"'{name}' ({type_name}) was not {verb} (visibility state {vis})",
                                    SwErrors.swFeatureError, {"feature": name, "type": type_name, "visible_state": vis})
            return self._result(True, f"{type_name} '{name}' {verb}", SwErrors.swSuccess,
                                {"feature": name, "type": type_name, "hidden": hide, "visible_state": vis})

        # Solid-body feature: hide/show every body that has faces created by it.
        bodies = []
        for face in (get_attr_or_call(feat, "GetFaces") or []):
            try:
                body = get_attr_or_call(face, "GetBody")
            except Exception:
                body = None
            if body is not None and not any(body is b or body == b for b in bodies):
                bodies.append(body)
        if not bodies:
            return self._result(False, f"'{name}' ({type_name}) is not reference geometry, a sketch, or a body feature with "
                                       "faces; nothing to hide/show", SwErrors.swInvalidInput)
        names = []
        for body in bodies:
            body.HideBody(bool(hide))
            names.append(str(get_attr_or_call(body, "Name") or ""))
        doc.GraphicsRedraw2()
        still = [n for b, n in zip(bodies, names) if bool(get_attr_or_call(b, "Visible")) == bool(hide)]
        if still:
            return self._result(False, f"Bodies not {verb}: {still}", SwErrors.swFeatureError, {"bodies": names})
        return self._result(True, f"{len(names)} body/bodies of '{name}' {verb}: {names}", SwErrors.swSuccess,
                            {"feature": name, "type": type_name, "bodies": names, "hidden": hide})

    def manage_feature(
        self,
        action: str,
        feature_name: str,
        new_name: Optional[str] = None,
        target_feature: Optional[str] = None,
        move_location: Optional[str] = "after",
        delete_absorbed: bool = True,
        delete_children: bool = False,
        config_scope: int = 1,
        color_rgb: Optional[List[int]] = None,
    ) -> Dict:
        """
        Manage feature tree operations: suppress, unsuppress, rename, delete, reorder, set_color.

        Args:
            action: 'suppress', 'unsuppress', 'rename', 'delete', 'reorder', 'set_color'
            feature_name: Name of feature to operate on
            new_name: New feature name for 'rename' action
            target_feature: Target feature name for 'reorder' action
            move_location: 'before', 'after', 'to_top', 'to_end' (default 'after')
            delete_absorbed: Delete absorbed sketches/features on delete (default True)
            delete_children: Delete child features on delete (default False)
            config_scope: Suppression scope: 1=ThisConfiguration, 2=AllConfigurations (default 1)
            color_rgb: RGB color [R, G, B] with integer values 0-255 for 'set_color' action
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            dtype = self.get_document_type(doc)
            if dtype == "Drawing":
                return self._result(
                    False,
                    "manage_feature is not supported for Drawing documents (requires Part or Assembly)",
                    SwErrors.swInvalidDocumentType,
                )

            action_clean = (action or "").strip().lower()
            if action_clean not in ("suppress", "unsuppress", "rename", "delete", "reorder", "set_color", "hide", "show"):
                return self._result(
                    False,
                    f"Invalid action '{action}'. Valid actions: 'suppress', 'unsuppress', 'rename', 'delete', 'reorder', 'set_color', 'hide', 'show'.",
                    SwErrors.swInvalidInput,
                )

            if not feature_name or not feature_name.strip():
                return self._result(False, "Missing required feature_name", SwErrors.swInvalidInput)

            target_feat_name = feature_name.strip()
            feat = doc.FeatureByName(target_feat_name)
            if not feat:
                return self._result(
                    False,
                    f"Feature '{target_feat_name}' not found in active document",
                    SwErrors.swFeatureError,
                )

            if action_clean in ("hide", "show"):
                return self._set_feature_visibility(doc, feat, target_feat_name, action_clean == "hide")

            if action_clean == "suppress":
                # SetSuppression2: 0 = swSuppressFeature
                feat.SetSuppression2(0, config_scope, None)
                get_attr_or_call(doc, "EditRebuild3")

                # Post-condition verification
                check_feat = doc.FeatureByName(target_feat_name)
                is_supp = False
                if check_feat:
                    supp_ret = check_feat.IsSuppressed2(config_scope, None)
                    is_supp = supp_ret[0] if isinstance(supp_ret, (list, tuple)) else bool(supp_ret)

                if not is_supp:
                    return self._result(
                        False,
                        f"Post-condition failed: Feature '{target_feat_name}' was not suppressed",
                        SwErrors.swFeatureError,
                    )

                return self._result(
                    True,
                    f"Successfully suppressed feature '{target_feat_name}'",
                    SwErrors.swSuccess,
                    {"feature": target_feat_name, "suppressed": True},
                )

            elif action_clean == "unsuppress":
                # SetSuppression2: 1 = swUnSuppressFeature
                feat.SetSuppression2(1, config_scope, None)
                get_attr_or_call(doc, "EditRebuild3")

                # Post-condition verification
                check_feat = doc.FeatureByName(target_feat_name)
                is_supp = True
                if check_feat:
                    supp_ret = check_feat.IsSuppressed2(config_scope, None)
                    is_supp = supp_ret[0] if isinstance(supp_ret, (list, tuple)) else bool(supp_ret)

                if is_supp:
                    return self._result(
                        False,
                        f"Post-condition failed: Feature '{target_feat_name}' is still suppressed",
                        SwErrors.swFeatureError,
                    )

                return self._result(
                    True,
                    f"Successfully unsuppressed feature '{target_feat_name}'",
                    SwErrors.swSuccess,
                    {"feature": target_feat_name, "suppressed": False},
                )

            elif action_clean == "rename":
                if not new_name or not new_name.strip():
                    return self._result(False, "Missing new_name for 'rename' action", SwErrors.swInvalidInput)
                n_name = new_name.strip()
                if n_name == target_feat_name:
                    return self._result(True, f"Feature name is already '{n_name}'", SwErrors.swSuccess)

                existing = doc.FeatureByName(n_name)
                if existing:
                    return self._result(False, f"A feature named '{n_name}' already exists", SwErrors.swInvalidInput)

                feat.Name = n_name
                get_attr_or_call(doc, "EditRebuild3")

                # Post-condition verification
                if not doc.FeatureByName(n_name) or doc.FeatureByName(target_feat_name):
                    return self._result(
                        False,
                        f"Post-condition failed: Feature '{target_feat_name}' could not be renamed to '{n_name}'",
                        SwErrors.swFeatureError,
                    )

                return self._result(
                    True,
                    f"Successfully renamed feature '{target_feat_name}' to '{n_name}'",
                    SwErrors.swSuccess,
                    {"old_name": target_feat_name, "new_name": n_name},
                )

            elif action_clean == "delete":
                doc.ClearSelection2(True)
                feat.Select2(False, 0)
                opt = (int(SwDeleteSelectionOptions.swDelete_Absorbed) if delete_absorbed else 0) | \
                      (int(SwDeleteSelectionOptions.swDelete_Children) if delete_children else 0)
                ext = getattr(doc, "Extension", None)
                if not ext:
                    return self._result(False, "Document Extension interface unavailable", SwErrors.swGeneralError)

                ok = ext.DeleteSelection2(opt)
                get_attr_or_call(doc, "EditRebuild3")

                # Post-condition verification
                if doc.FeatureByName(target_feat_name) is not None:
                    return self._result(
                        False,
                        f"Post-condition failed: Feature '{target_feat_name}' still exists after delete",
                        SwErrors.swFeatureError,
                    )

                return self._result(
                    True,
                    f"Successfully deleted feature '{target_feat_name}'",
                    SwErrors.swSuccess,
                    {"deleted_feature": target_feat_name},
                )

            elif action_clean == "reorder":
                loc_clean = str(move_location).strip().lower()
                if (not target_feature or not target_feature.strip()) and loc_clean in ("to_end", "to_top"):
                    # to_end/to_top need no target: anchor on the last / first solid feature.
                    names = []
                    f_it = doc.FirstFeature
                    while f_it is not None:
                        try:
                            if get_attr_or_call(f_it, "GetTypeName2") not in (
                                "CommentsFolder", "FavoriteFolder", "HistoryFolder", "SelectionSetFolder",
                                "SensorFolder", "DocsFolder", "DetailCabinet", "SurfaceBodyFolder",
                                "SolidBodyFolder", "EnvFolder", "InkMarkupFolder", "EqnFolder",
                                "MaterialFolder", "RefPlane", "OriginProfileFeature",
                            ):
                                names.append(f_it.Name)
                            f_it = f_it.GetNextFeature
                        except Exception:
                            break
                    names = [n for n in names if n != target_feat_name]
                    if not names:
                        return self._result(False, "No other feature to reorder against.", SwErrors.swFeatureError)
                    target_feature = names[-1] if loc_clean == "to_end" else names[0]
                    move_location = "after" if loc_clean == "to_end" else "before"
                if not target_feature or not target_feature.strip():
                    return self._result(False, "Missing target_feature for 'reorder' action", SwErrors.swInvalidInput)
                tgt_name = target_feature.strip()
                tgt_feat = doc.FeatureByName(tgt_name)
                if not tgt_feat:
                    return self._result(False, f"Target feature '{tgt_name}' not found", SwErrors.swFeatureError)

                loc_map = {
                    "to_end": 1,    # swMoveToEnd
                    "before": 2,    # swMoveBefore
                    "after": 3,     # swMoveAfter
                    "to_top": 4,    # swMoveToTop
                }
                move_loc = loc_map.get(str(move_location).strip().lower(), 3)

                ext = getattr(doc, "Extension", None)
                if not ext:
                    return self._result(False, "Document Extension interface unavailable", SwErrors.swGeneralError)

                ok = ext.ReorderFeature(target_feat_name, tgt_name, move_loc)
                get_attr_or_call(doc, "EditRebuild3")

                if not ok:
                    return self._result(
                        False,
                        f"ReorderFeature failed: cannot move '{target_feat_name}' {move_location} '{tgt_name}' (geometric dependency constraint)",
                        SwErrors.swFeatureError,
                    )

                return self._result(
                    True,
                    f"Successfully reordered feature '{target_feat_name}' {move_location} '{tgt_name}'",
                    SwErrors.swSuccess,
                    {"feature": target_feat_name, "target_feature": tgt_name, "location": move_location},
                )

            elif action_clean == "set_color":
                if not color_rgb or len(color_rgb) < 3:
                    return self._result(
                        False,
                        "Action 'set_color' requires color_rgb=[R, G, B] (integers 0-255)",
                        SwErrors.swInvalidInput,
                    )
                try:
                    r_val = max(0.0, min(1.0, float(color_rgb[0]) / 255.0))
                    g_val = max(0.0, min(1.0, float(color_rgb[1]) / 255.0))
                    b_val = max(0.0, min(1.0, float(color_rgb[2]) / 255.0))

                    import pythoncom, win32com.client
                    arr = win32com.client.VARIANT(
                        pythoncom.VT_ARRAY | pythoncom.VT_R8,
                        [r_val, g_val, b_val, 0.5, 0.6, 0.4, 0.3, 0.0, 0.0]
                    )
                    null_callout = null_dispatch()
                    feat.SetMaterialPropertyValues2(arr, config_scope, null_callout)
                    get_attr_or_call(doc, "GraphicsRedraw2")

                    return self._result(
                        True,
                        f"Successfully applied color RGB {color_rgb} to feature '{target_feat_name}'",
                        SwErrors.swSuccess,
                        {"feature": target_feat_name, "color_rgb": color_rgb},
                    )
                except Exception as ce:
                    return self._result(
                        False,
                        f"Failed to set color on feature '{target_feat_name}': {ce}",
                        SwErrors.swFeatureError,
                    )

        except Exception as e:
            logger.error(f"manage_feature error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"manage_feature error: {e}", SwErrors.swGeneralError)

    def revolve_sketch(
        self,
        angle: float = 360.0,
        reverse_dir: bool = False,
        is_thin: bool = False,
        thin_thickness: float = 1.0,
        is_cut: bool = False,
        axis_name: Optional[str] = None,
        unit: str = "mm",
    ) -> Dict:
        """
        Revolve active sketch around a centerline or specified axis (IFeatureManager.FeatureRevolve2).
        """
        import math
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            dtype = self.get_document_type(doc)
            if dtype not in ("Part", "Assembly"):
                return self._result(False, "revolve_sketch requires a Part or Assembly document", SwErrors.swInvalidDocumentType)

            # Step 1: Close sketch and select it
            success, sketch_name, error_msg = self._close_and_select_sketch(doc)
            if not success:
                return self._result(False, f"revolve_sketch failed: {error_msg}", SwErrors.swFeatureError)

            # If an axis is specified, select it with mark 4
            if axis_name and axis_name.strip():
                name_str = axis_name.strip()
                empty_callout = null_dispatch()
                if not doc.Extension.SelectByID2(name_str, "AXIS", 0, 0, 0, True, 4, empty_callout, 0):
                    if not doc.Extension.SelectByID2(name_str, "EDGE", 0, 0, 0, True, 4, empty_callout, 0):
                        doc.Extension.SelectByID2(name_str, "SKETCHSEGMENT", 0, 0, 0, True, 4, empty_callout, 0)

            angle_rad = math.radians(float(angle))
            thick_m = self._units.to_meters(thin_thickness, unit) if is_thin else 0.0

            pre_state = capture_pre_mutation_state(doc)

            feat = doc.FeatureManager.FeatureRevolve2(
                True,          # SingleDir
                True,          # IsSolid
                bool(is_thin), # IsThin
                bool(is_cut),  # IsCut
                bool(reverse_dir), # ReverseDir
                False,         # BothDirectionUpToSameEntity
                0,             # Dir1Type (swEndCondBlind = 0)
                0,             # Dir2Type
                angle_rad,     # Dir1Angle
                0.0,           # Dir2Angle
                False,         # OffsetReverse1
                False,         # OffsetReverse2
                0.0,           # OffsetDistance1
                0.0,           # OffsetDistance2
                0,             # ThinType
                thick_m,       # ThinThickness1
                0.0,           # ThinThickness2
                True,          # Merge
                False,         # UseFeatScope
                True,          # UseAutoSelect
            )
            get_attr_or_call(doc, "EditRebuild3")

            ok, post_msg, post_diag = verify_mutation_post_condition(doc, feat, pre_state, "feature")
            if not ok:
                return self._result(False, f"revolve_sketch failed post-condition verification: {post_msg}", SwErrors.swFeatureError, post_diag)

            feat_name = getattr(feat, "Name", "Revolve")
            return self._result(
                True,
                f"Successfully revolved sketch ({angle}°)",
                SwErrors.swSuccess,
                {"feature_name": feat_name, "angle": angle, "is_cut": is_cut},
            )
        except Exception as e:
            logger.error(f"revolve_sketch error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"revolve_sketch error: {e}", SwErrors.swGeneralError)

    def create_shell(
        self,
        thickness: float = 1.0,
        outward: bool = False,
        unit: str = "mm",
        face_indices: Optional[List[int]] = None,
        body_index: int = 1,
    ) -> Dict:
        """
        Create a shell feature on active part (IModelDoc2.InsertFeatureShell).
        Faces to remove (open): face_indices (1-based, list_topology) or the currently selected face(s).
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            dtype = self.get_document_type(doc)
            if dtype != "Part":
                return self._result(False, "create_shell requires a Part document", SwErrors.swInvalidDocumentType)

            if float(thickness) <= 0:
                return self._result(False, f"Invalid shell thickness {thickness}: thickness must be greater than 0.", SwErrors.swInvalidParameter)

            thick_m = self._units.to_meters(thickness, unit)
            bodies = list(doc.GetBodies2(0, False) or ())
            if face_indices:
                if not (1 <= int(body_index) <= len(bodies)):
                    return self._result(False, f"body_index {body_index} out of range (1..{len(bodies)}).",
                                        SwErrors.swInvalidInput)
                faces = list(get_attr_or_call(bodies[int(body_index) - 1], "GetFaces") or ())
                bad = [i for i in face_indices if not (1 <= int(i) <= len(faces))]
                if bad:
                    return self._result(False, f"face_indices {bad} out of range (1..{len(faces)}).",
                                        SwErrors.swInvalidInput)
                doc.ClearSelection2(True)
                for i in face_indices:
                    faces[int(i) - 1].Select2(True, 0)
            n_sel = 0
            try:
                sel_mgr = doc.SelectionManager
                n_sel = sum(1 for i in range(1, sel_mgr.GetSelectedObjectCount2(-1) + 1)
                            if int(sel_mgr.GetSelectedObjectType3(i, -1)) == 2)  # swSelFACES
            except Exception:
                pass
            pre_state = capture_pre_mutation_state(doc)

            doc.InsertFeatureShell(thick_m, bool(outward))
            get_attr_or_call(doc, "EditRebuild3")

            feat = None
            try:
                feats = doc.FeatureManager.GetFeatures(True)
                if feats:
                    feat = feats[-1]
            except Exception:
                pass

            ok, post_msg, post_diag = verify_mutation_post_condition(doc, feat, pre_state, "feature" if feat else "general")
            if not ok:
                why = []
                if n_sel == 0:
                    why.append("no face was selected: pass face_indices (from list_topology) or select the "
                               "face(s) to open first")
                if len(bodies) > 1:
                    why.append(f"the part has {len(bodies)} solid bodies and a shell works on one body (the "
                               "one owning the selected faces)")
                why.append(f"thickness {thickness}{unit} may be larger than the smallest radius or wall it must offset")
                return self._result(
                    False,
                    f"Shell was not created ({post_msg}). Likely cause: " + "; ".join(why) + ".",
                    SwErrors.swFeatureError, post_diag)

            feat_name = getattr(feat, "Name", "Shell1") if feat else "Shell1"
            return self._result(
                True,
                f"Successfully created shell (thickness={thickness}{unit}, {n_sel} face(s) removed)",
                SwErrors.swSuccess,
                {"feature_name": feat_name, "thickness": thickness, "unit": unit, "faces_removed": n_sel},
            )
        except Exception as e:
            logger.error(f"create_shell error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"create_shell error: {e}", SwErrors.swGeneralError)

    def create_pattern(
        self,
        pattern_type: str = "linear",
        feature_name: Optional[str] = None,
        num1: int = 2,
        spacing1: float = 10.0,
        flip_dir1: bool = False,
        num2: int = 1,
        spacing2: float = 0.0,
        flip_dir2: bool = False,
        angle: float = 360.0,
        equal_spacing: bool = True,
        unit: str = "mm",
        direction1: Optional[str] = None,
        direction2: Optional[str] = None,
        mirror_plane: Optional[str] = None,
        merge: bool = True,
    ) -> Dict:
        """
        Create a linear or circular pattern feature (IFeatureManager.FeatureLinearPattern4 / FeatureCircularPattern4).
        direction1/direction2 ('+x', '-y', 'z', ...) make a linear pattern run along world axes, verified.
        """
        import math
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            ptype = str(pattern_type).strip().lower()
            pre_state = capture_pre_mutation_state(doc)

            if ptype == "mirror":
                return self._mirror_about_plane(doc, pre_state, feature_name, mirror_plane, merge)

            if ptype == "linear" and direction1:
                return self._linear_pattern_along_axes(
                    doc, pre_state, feature_name, int(num1), self._units.to_meters(spacing1, unit),
                    direction1, int(num2), self._units.to_meters(spacing2, unit) if int(num2) > 1 else 0.0,
                    direction2)

            if ptype == "linear":
                num1_val = int(num1)
                spacing1_m = self._units.to_meters(spacing1, unit)
                num2_val = int(num2)
                spacing2_m = self._units.to_meters(spacing2, unit) if num2_val > 1 else 0.0

                if feature_name:
                    feat_obj = doc.FeatureByName(feature_name.strip())
                    if feat_obj:
                        feat_obj.Select2(True, 4)
                    else:
                        empty_callout = null_dispatch()
                        doc.Extension.SelectByID2(feature_name.strip(), "BODYFEATURE", 0, 0, 0, True, 4, empty_callout, 0)

                sel_mgr = doc.SelectionManager
                has_mark1 = False
                for i in range(1, sel_mgr.GetSelectedObjectCount2(-1) + 1):
                    if sel_mgr.GetSelectedObjectMark(i) == 1:
                        has_mark1 = True
                        break

                if not has_mark1:
                    bodies = doc.GetBodies2(0, False) or ()
                    for b in bodies:
                        edges = get_attr_or_call(b, "GetEdges") or ()
                        # First try: find an edge parallel to X axis (length > 30mm)
                        for e in edges:
                            try:
                                v1 = get_attr_or_call(e, "GetStartVertex")
                                v2 = get_attr_or_call(e, "GetEndVertex")
                                if v1 and v2:
                                    p1 = get_attr_or_call(v1, "GetPoint")
                                    p2 = get_attr_or_call(v2, "GetPoint")
                                    if p1 and p2:
                                        dx = abs(p1[0] - p2[0])
                                        dy = abs(p1[1] - p2[1])
                                        dz = abs(p1[2] - p2[2])
                                        if dx > 0.03 and dy < 1e-3 and dz < 1e-3:
                                            e.Select2(True, 1)
                                            has_mark1 = True
                                            break
                            except Exception:
                                pass
                        if has_mark1:
                            break

                        # Fallback: any linear edge
                        if not has_mark1:
                            for e in edges:
                                try:
                                    crv = get_attr_or_call(e, "GetCurve")
                                    is_ln = False
                                    if crv is not None:
                                        # IsLine / Identity are properties under late binding.
                                        is_ln = bool(get_attr_or_call(crv, "IsLine")) or get_attr_or_call(crv, "Identity") == 3001
                                    if is_ln:
                                        e.Select2(True, 1)
                                        has_mark1 = True
                                        break
                                except Exception:
                                    pass
                            if has_mark1:
                                break

                feat = None
                for flip_try in (bool(flip_dir1), not bool(flip_dir1)):
                    try:
                        feat = doc.FeatureManager.FeatureLinearPattern2(
                            num1_val, spacing1_m,
                            num2_val, spacing2_m,
                            flip_try, bool(flip_dir2),
                            "", "",
                            False,
                        )
                        if feat:
                            break
                    except Exception:
                        pass
                    if not feat:
                        try:
                            feat = doc.FeatureManager.FeatureLinearPattern(
                                num1_val, spacing1_m,
                                num2_val, spacing2_m,
                                flip_try, bool(flip_dir2),
                                "", "",
                            )
                            if feat:
                                break
                        except Exception:
                            pass
            elif ptype == "circular":
                num_val = int(num1)
                angle_rad = math.radians(float(angle))

                # Re-mark whatever the caller pre-selected: the pattern API needs the axis
                # (edge / cylindrical face / axis) with mark 1 and seed features with mark 4.
                sel_mgr = doc.SelectionManager
                pre_selected = []
                for i in range(1, sel_mgr.GetSelectedObjectCount2(-1) + 1):
                    try:
                        pre_selected.append((
                            sel_mgr.GetSelectedObject6(i, -1),
                            int(sel_mgr.GetSelectedObjectType3(i, -1)),
                            int(sel_mgr.GetSelectedObjectMark(i)),
                        ))
                    except Exception:
                        pass
                doc.ClearSelection2(True)

                AXIS_TYPES = (1, 2, 5)  # swSelEDGES, swSelFACES, swSelDATUMAXES
                has_mark1 = False
                seed_selected = False
                for obj, sel_type, mark in pre_selected:
                    if obj is None:
                        continue
                    try:
                        if sel_type in AXIS_TYPES and not has_mark1:
                            has_mark1 = bool(obj.Select2(True, 1))
                        elif sel_type not in AXIS_TYPES and not feature_name:
                            seed_selected = bool(obj.Select2(True, 4)) or seed_selected
                    except Exception:
                        pass

                if feature_name:
                    feat_obj = doc.FeatureByName(feature_name.strip())
                    if feat_obj:
                        seed_selected = bool(feat_obj.Select2(True, 4))
                    else:
                        empty_callout = null_dispatch()
                        seed_selected = bool(doc.Extension.SelectByID2(
                            feature_name.strip(), "BODYFEATURE", 0, 0, 0, True, 4, empty_callout, 0
                        ))
                if not seed_selected:
                    return self._result(
                        False,
                        f"create_pattern: seed feature '{feature_name}' not found (pass feature_name or pre-select a feature).",
                        SwErrors.swSelectionFailed,
                    )

                if not has_mark1:
                    # No axis given: fall back to the largest cylindrical face of the model.
                    best_face, best_area = None, -1.0
                    bodies = doc.GetBodies2(0, False) or ()
                    for b in bodies:
                        faces = get_attr_or_call(b, "GetFaces") or ()
                        for f in faces:
                            try:
                                surf = get_attr_or_call(f, "GetSurface")
                                # IsCylinder is a property under late binding (calling it raises).
                                if surf is not None and bool(get_attr_or_call(surf, "IsCylinder")):
                                    area = float(get_attr_or_call(f, "GetArea") or 0.0)
                                    if area > best_area:
                                        best_face, best_area = f, area
                            except Exception:
                                pass
                    if best_face is not None:
                        has_mark1 = bool(best_face.Select2(True, 1))

                if not has_mark1:
                    return self._result(
                        False,
                        "create_pattern: circular pattern needs an axis. Pre-select a cylindrical face, "
                        "circular/linear edge or axis (e.g. with select_by_geometry) before calling.",
                        SwErrors.swSelectionFailed,
                    )

                feat = None
                try:
                    feat = doc.FeatureManager.FeatureCircularPattern4(
                        num_val, angle_rad, bool(flip_dir1), "", False, bool(equal_spacing), False
                    )
                except Exception:
                    try:
                        feat = doc.FeatureManager.FeatureCircularPattern3(
                            num_val, angle_rad, bool(flip_dir1), "", False, bool(equal_spacing)
                        )
                    except Exception:
                        feat = doc.FeatureManager.FeatureCircularPattern2(
                            num_val, angle_rad, bool(flip_dir1), "", False
                        )
            else:
                return self._result(False, f"Unsupported pattern_type '{pattern_type}'. Allowed: 'linear', 'circular'", SwErrors.swInvalidInput)

            get_attr_or_call(doc, "EditRebuild3")

            if not feat:
                try:
                    feats = doc.FeatureManager.GetFeatures(True)
                    if feats:
                        feat = feats[-1]
                except Exception:
                    pass

            ok, post_msg, post_diag = verify_mutation_post_condition(doc, feat, pre_state, "feature" if feat else "general")
            if not ok:
                return self._result(False, f"create_pattern failed post-condition verification: {post_msg}", SwErrors.swFeatureError, post_diag)

            feat_name = getattr(feat, "Name", f"{ptype.capitalize()}Pattern")
            return self._result(
                True,
                f"Successfully created {ptype} pattern",
                SwErrors.swSuccess,
                {"feature_name": feat_name, "pattern_type": ptype, "count": num1},
            )
        except Exception as e:
            logger.error(f"create_pattern error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"create_pattern error: {e}", SwErrors.swGeneralError)

    @staticmethod
    def _parse_axis(text: Optional[str]):
        """'+x' / '-Y' / 'z' -> (axis index, sign). None when not a world-axis spec."""
        t = str(text or "").strip().lower()
        sign = -1.0 if t.startswith("-") else 1.0
        t = t.lstrip("+-")
        return ({"x": 0, "y": 1, "z": 2}[t], sign) if t in ("x", "y", "z") else None

    def _axis_reference(self, doc, axis_i: int):
        """A reference entity whose direction is the world axis: a reference plane normal to it."""
        feat = get_attr_or_call(doc, "FirstFeature")
        while feat is not None:
            try:
                if get_attr_or_call(feat, "GetTypeName2") == "RefPlane":
                    plane = get_attr_or_call(feat, "GetSpecificFeature2")
                    a = get_attr_or_call(get_attr_or_call(plane, "Transform"), "ArrayData")
                    normal = [float(a[6]), float(a[7]), float(a[8])]
                    if abs(abs(normal[axis_i]) - 1.0) < 1e-9:
                        return feat
            except Exception:
                pass
            feat = get_attr_or_call(feat, "GetNextFeature")
        return None

    def _mirror_about_plane(self, doc, pre_state, feature_name, mirror_plane, merge: bool = True) -> Dict:
        """
        Mirror features (feature_name, comma-separated for several) or, without feature_name, every
        solid body about a plane: 'Right' (world YZ, flips X), 'Top' (XZ, flips Y), 'Front' (XY,
        flips Z) or the name of any reference plane.
        """
        key = str(mirror_plane or "").strip()
        if not key:
            return self._result(False, "mirror needs mirror_plane: 'Right', 'Top', 'Front' or a plane name.",
                                SwErrors.swInvalidInput)
        std = {"right": 0, "yz": 0, "x": 0, "right plane": 0, "top": 1, "xz": 1, "y": 1, "top plane": 1,
               "front": 2, "xy": 2, "z": 2, "front plane": 2}
        plane = None
        if key.lower() in std:
            plane = self._axis_reference(doc, std[key.lower()])
        if plane is None:
            plane = doc.FeatureByName(key)
        if plane is None:
            return self._result(False, f"Mirror plane '{mirror_plane}' not found.", SwErrors.swSelectionFailed)

        seeds = []
        for nm in [s.strip() for s in str(feature_name or "").split(",") if s.strip()]:
            f = doc.FeatureByName(nm)
            if f is None:
                return self._result(False, f"Feature '{nm}' not found.", SwErrors.swSelectionFailed)
            seeds.append(f)
        mirror_body = not seeds

        doc.ClearSelection2(True)
        plane.Select2(False, 2)
        if mirror_body:
            # Bodies are selected by name: IBody2.Select2 needs a SelectData object, which cannot be
            # created under late binding ("Member not found").
            n_bodies = 0
            for b in (doc.GetBodies2(0, False) or ()):
                if doc.Extension.SelectByID2(str(b.Name), "SOLIDBODY", 0, 0, 0, True, 256, null_dispatch(), 0):
                    n_bodies += 1
            if not n_bodies:
                return self._result(False, "Mirror: no solid body could be selected.", SwErrors.swSelectionFailed)
        else:
            for f in seeds:
                f.Select2(True, 1)
        feat = None
        try:
            # InsertMirrorFeature2(MirrorBody, GeometryPattern, Merge, Knit, ScopeOptions)
            feat = doc.FeatureManager.InsertMirrorFeature2(mirror_body, False, bool(merge) or not mirror_body, False, 0)
        except Exception as ex:
            logger.debug(f"InsertMirrorFeature2 failed: {ex}")
            try:
                feat = doc.FeatureManager.InsertMirrorFeature(mirror_body, False, True, False)
            except Exception as ex2:
                return self._result(False, f"Mirror failed: {ex2}", SwErrors.swFeatureError)
        get_attr_or_call(doc, "EditRebuild3")
        if not feat:
            return self._result(
                False,
                "Mirror was not created (SolidWorks returned no feature). The mirrored copy must touch or "
                "overlap the body it merges with, and mirrored features must land on the body.",
                SwErrors.swFeatureError)
        ok, post_msg, post_diag = verify_mutation_post_condition(doc, feat, pre_state, "feature")
        if not ok:
            return self._result(False, f"Mirror failed post-condition verification: {post_msg}",
                                SwErrors.swFeatureError, post_diag)
        what = "all solid bodies" if mirror_body else ", ".join(f.Name for f in seeds)
        return self._result(True, f"Mirrored {what} about '{plane.Name}'", SwErrors.swSuccess,
                            {"feature_name": feat.Name, "pattern_type": "mirror", "mirror_plane": plane.Name,
                             "mirrored": what})

    def _linear_pattern_along_axes(self, doc, pre_state, feature_name, n1, s1_m, direction1, n2, s2_m, direction2) -> Dict:
        """Linear pattern along world axes, with the resulting direction checked (and flipped if needed)."""
        d1 = self._parse_axis(direction1)
        d2 = self._parse_axis(direction2) if (n2 > 1 and direction2) else None
        if d1 is None or (n2 > 1 and d2 is None):
            return self._result(
                False, "direction1 (and direction2 when num2 > 1) must be one of x, y, z, +x, -x, +y, -y, +z, -z.",
                SwErrors.swInvalidInput)
        if not feature_name:
            return self._result(False, "feature_name (the seed feature) is required with direction1.",
                                SwErrors.swInvalidInput)
        seed = doc.FeatureByName(feature_name.strip())
        if seed is None:
            return self._result(False, f"Seed feature '{feature_name}' not found.", SwErrors.swSelectionFailed)
        ref1 = self._axis_reference(doc, d1[0])
        ref2 = self._axis_reference(doc, d2[0]) if d2 else None
        if ref1 is None or (d2 and ref2 is None):
            return self._result(False, "No reference plane normal to the requested world axis was found.",
                                SwErrors.swSelectionFailed)

        def mass():
            try:
                mp = get_attr_or_call(doc, "GetMassProperties")
                return [float(mp[0]), float(mp[1]), float(mp[2])], float(mp[3])
            except Exception:
                return None, None

        def seed_center():
            lo, hi = [float("inf")] * 3, [float("-inf")] * 3
            try:
                for f in (get_attr_or_call(seed, "GetFaces") or ()):
                    b = get_attr_or_call(f, "GetBox")
                    for i in range(3):
                        lo[i], hi[i] = min(lo[i], b[i]), max(hi[i], b[i + 3])
            except Exception:
                return None
            return None if lo[0] == float("inf") else [(lo[i] + hi[i]) / 2.0 for i in range(3)]

        c_seed = seed_center()
        com0, v0 = mass()

        def build(flip1: bool, flip2: bool):
            doc.ClearSelection2(True)
            ref1.Select2(False, 1)
            if ref2 is not None:
                ref2.Select2(True, 2)
            seed.Select2(True, 4)
            try:
                return doc.FeatureManager.FeatureLinearPattern2(
                    n1, s1_m, n2, s2_m, flip1, flip2, "", "", False)
            except Exception:
                return doc.FeatureManager.FeatureLinearPattern(n1, s1_m, n2, s2_m, flip1, flip2, "", "")

        def measured_signs():
            """Sign of the pattern along each requested axis, from where the volume change happened."""
            com1, v1 = mass()
            if c_seed is None or com0 is None or com1 is None or abs(v1 - v0) < 1e-13:
                return None
            change_c = [(v1 * com1[i] - v0 * com0[i]) / (v1 - v0) for i in range(3)]
            return [change_c[i] - c_seed[i] for i in range(3)]

        flip1 = flip2 = False
        feat = None
        verified = False
        for _attempt in range(3):
            feat = build(flip1, flip2)
            if not feat:
                break
            get_attr_or_call(doc, "EditRebuild3")
            off = measured_signs()
            if off is None:
                break
            wrong1 = off[d1[0]] * d1[1] < -1e-7
            wrong2 = bool(d2) and off[d2[0]] * d2[1] < -1e-7
            if not wrong1 and not wrong2:
                verified = True
                break
            # Wrong side: remove and rebuild flipped.
            doc.ClearSelection2(True)
            feat.Select2(False, 0)
            doc.Extension.DeleteSelection2(0)
            feat = None
            flip1, flip2 = flip1 != wrong1, flip2 != wrong2
        if not feat:
            return self._result(
                False,
                f"Linear pattern of '{feature_name}' along {direction1}"
                + (f" / {direction2}" if d2 else "") + " could not be created (SolidWorks returned no feature). "
                "Check that the instances stay on the body.",
                SwErrors.swFeatureError)
        ok, post_msg, post_diag = verify_mutation_post_condition(doc, feat, pre_state, "feature")
        if not ok:
            return self._result(False, f"create_pattern failed post-condition verification: {post_msg}",
                                SwErrors.swFeatureError, post_diag)
        names = "+X +Y +Z".split(), "-X -Y -Z".split()
        lab = lambda d: (names[0] if d[1] > 0 else names[1])[d[0]]  # noqa: E731
        return self._result(
            True,
            f"Linear pattern of '{feature_name}': {n1} x {round(s1_m * 1000.0, 4)} mm along world {lab(d1)}"
            + (f", {n2} x {round(s2_m * 1000.0, 4)} mm along world {lab(d2)}" if d2 else "")
            + ("" if verified else " (direction could not be verified: no volume change measured)"),
            SwErrors.swSuccess,
            {"feature_name": feat.Name, "pattern_type": "linear", "count": n1 * max(n2, 1),
             "direction1": lab(d1), "direction2": lab(d2) if d2 else None, "direction_verified": verified})

    def create_hole_wizard(
        self,
        hole_type: str = "counterbore",
        standard: str = "ISO",
        fastener_type: str = "Hex Bolt",
        size: str = "M6",
        end_condition: str = "through_all",
        depth: Optional[float] = None,
        diameter: Optional[float] = None,
        unit: str = "mm",
    ) -> Dict:
        """
        Create a standard hole using Hole Wizard (IFeatureManager.HoleWizard5).
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            dtype = self.get_document_type(doc)
            if dtype != "Part":
                return self._result(False, "create_hole_wizard requires a Part document", SwErrors.swInvalidDocumentType)

            type_map = {
                "counterbore": 0,
                "countersink": 1,
                "hole": 2,
                "standard": 2,
                "pipe_tap": 3,
                "tap": 4,
            }
            htype_int = type_map.get(str(hole_type).lower(), 0)

            std_map = {
                "ansi_inch": 0,
                "ansi_metric": 1,
                "bsi": 2,
                "dme": 3,
                "din": 4,
                "iso": 8,
            }
            std_key = str(standard).strip().lower().replace(" ", "_").replace("-", "_")
            if std_key not in std_map:
                return self._result(
                    False,
                    f"Unsupported hole standard '{standard}'. Use one of: ISO, DIN, ANSI Metric, ANSI Inch, BSI, DME.",
                    SwErrors.swInvalidParameter,
                )
            std_int = std_map[std_key]
            if str(hole_type).lower() not in type_map:
                return self._result(
                    False,
                    f"Unsupported hole_type '{hole_type}'. Use one of: {', '.join(type_map)}.",
                    SwErrors.swInvalidParameter,
                )

            end_cond_map = {
                "blind": 0,
                "through_all": 1,
                "through_next": 2,
            }
            end_int = end_cond_map.get(str(end_condition).lower(), 1)

            # Map (standard, hole_type) to default fastener type index from swconst.tlb
            fastener_table = {
                (8, 0): 139,  # ISO Socket Head Cap Screw
                (8, 1): 141,  # ISO CTSK Flat Head
                (8, 2): 143,  # ISO Drill Sizes
                (8, 3): 148,  # ISO Tapered Pipe Tap
                (8, 4): 147,  # ISO Tapped Hole
                (4, 0): 67,   # DIN Socket Head Cap
                (4, 1): 69,   # DIN CTSK Flat Head
                (4, 2): 72,   # DIN Drill Sizes
                (4, 3): 77,   # DIN Tapered Pipe Tap
                (4, 4): 76,   # DIN Tapped Hole
                (1, 0): 33,   # AnsiMetric Socket Head Cap
                (1, 1): 36,   # AnsiMetric Flat Head
                (1, 2): 39,   # AnsiMetric Drill Sizes
                (1, 4): 43,   # AnsiMetric Tapped Hole
                (0, 0): 9,    # AnsiInch Socket Cap Screw
                (0, 1): 15,   # AnsiInch Flat Head 82
                (0, 2): 18,   # AnsiInch All Drill Sizes
            }
            fastener_idx = fastener_table.get((std_int, htype_int), 139 if std_int == 8 else 0)

            if end_int == 0 and not depth:
                return self._result(
                    False, "create_hole_wizard: end_condition 'blind' needs a depth.", SwErrors.swInvalidParameter
                )

            # -1 tells HoleWizard5 to use the standard's own value for that slot. The old code
            # always passed 6 mm / 20 mm and zeros for Value1..12, which SolidWorks turned into
            # 1-inch-radius full-height holes.
            DEFAULT = -1.0
            import re as _re
            m_nom = _re.match(r"^\s*M\s*(\d+(?:\.\d+)?)", str(size), _re.I)
            nominal = float(m_nom.group(1)) if m_nom else None

            diam_m = self._units.to_meters(diameter, unit) if diameter else DEFAULT
            if diam_m == DEFAULT and htype_int == 2 and nominal:
                # Plain "hole" uses drill-size names, so "M5" is unknown and SolidWorks fell back to
                # its first drill size (Ø0.15 mm). Use the ISO 273 medium clearance hole instead.
                clearance = {3: 3.4, 4: 4.5, 5: 5.5, 6: 6.6, 8: 9.0, 10: 11.0, 12: 13.5, 14: 15.5,
                             16: 17.5, 20: 22.0, 24: 26.0, 30: 33.0}
                d_mm = clearance.get(int(nominal), nominal * 1.1)
                diam_m = d_mm / 1000.0
            depth_m = self._units.to_meters(depth, unit) if depth else DEFAULT

            # Tapped blind hole: `Depth` is the tap-drill depth; the thread depth (`Length`) defaulted
            # to the standard's 2xD, deeper than the drill, leaving a full-diameter bore with an island.
            length_m = DEFAULT
            if htype_int == 4 and end_int == 0 and depth and nominal:
                pitch = {3: 0.5, 4: 0.7, 5: 0.8, 6: 1.0, 8: 1.25, 10: 1.5, 12: 1.75, 14: 2.0,
                         16: 2.0, 20: 2.5, 24: 3.0, 30: 3.5}.get(int(nominal), nominal * 0.15)
                depth_mm = depth_m * 1000.0
                thread_mm = max(depth_mm - 2.0 * pitch, 0.5 * depth_mm)
                length_m = thread_mm / 1000.0

            pre_state = capture_pre_mutation_state(doc)

            feat = doc.FeatureManager.HoleWizard5(
                htype_int,
                std_int,
                fastener_idx,
                str(size),
                end_int,
                diam_m,
                depth_m,
                length_m,
                DEFAULT, DEFAULT, DEFAULT, DEFAULT, DEFAULT, DEFAULT,
                DEFAULT, DEFAULT, DEFAULT, DEFAULT, DEFAULT, DEFAULT,
                "",
                False,
                False,
                True,
                False,
                False,
                False,
            )
            get_attr_or_call(doc, "EditRebuild3")

            ok, post_msg, post_diag = verify_mutation_post_condition(doc, feat, pre_state, "feature")
            if not ok:
                return self._result(
                    False,
                    f"Hole Wizard hole was not created ({post_msg}). It is placed at a point picked on a planar "
                    "face: first call select {\"by\": \"point\", \"target_type\": \"face\", \"point\": [x, y, z]} with "
                    "the hole centre on the face, then create_hole_wizard. For plain, counterbored or tapped-size "
                    "holes at exact coordinates, hole_linear is simpler and verifies the result.",
                    SwErrors.swFeatureError, post_diag)

            # HoleWizard5 ignores the thread depth for tapped holes (it keeps the standard 2xD, deeper
            # than a short tap drill, leaving a full-diameter bore with an island). Fix it on the
            # feature definition: verified to give thread 8 / drill 10 for M6 x 10.
            if feat is not None and length_m != DEFAULT:
                try:
                    fdef = get_attr_or_call(feat, "GetDefinition")
                    fdef.AccessSelections(doc, null_dispatch())
                    fdef.ThreadDepth = float(length_m)
                    if not feat.ModifyDefinition(fdef, doc, null_dispatch()):
                        fdef.ReleaseSelectionAccess()
                        logger.warning("create_hole_wizard: ModifyDefinition(ThreadDepth) returned False")
                    get_attr_or_call(doc, "EditRebuild3")
                except Exception as td_ex:
                    logger.warning(f"create_hole_wizard: could not set thread depth: {td_ex}")

            # Plain holes made from an M-size are named after SolidWorks' fallback drill size
            # ("Ø0.15 (0.15) Diameter Hole1"); give them a truthful name.
            if feat is not None and htype_int == 2 and nominal and not diameter:
                try:
                    feat.Name = f"M{nominal:g} Clearance Hole Ø{diam_m * 1000:g}"
                except Exception:
                    pass

            # Sanity check the material removed against the nominal size, so a wrong hole is
            # reported as a failure instead of "[SUCCESS]".
            removed = None
            try:
                from ..core.post_conditions import capture_vol_state as _cvs
                after = _cvs(doc)
                before = getattr(pre_state, "vol_state", None)
                if after and before:
                    removed = before[0] - after[0]
            except Exception:
                removed = None
            if removed is not None and nominal:
                nominal_mm = nominal
                bbox_h = 0.0
                try:
                    box = doc.GetPartBox(True)
                    bbox_h = max(abs(box[3] - box[0]), abs(box[4] - box[1]), abs(box[5] - box[2])) * 1000.0
                except Exception:
                    pass
                hole_len = (float(depth) if (end_int == 0 and depth) else bbox_h) or bbox_h
                # Generous ceiling: counterbore/countersink head ≈ 2× nominal over the full length.
                ceiling = 3.1416 * (nominal_mm) ** 2 * max(hole_len, 1.0)
                # Floor: at least half a tap-drill-sized cylinder (0.8 x nominal) over the blind
                # depth, or over 1 mm for through holes (catches the Ø0.15 mm fallback hole).
                floor_len = float(depth) if (end_int == 0 and depth) else 1.0
                floor = 0.5 * 3.1416 * (0.4 * nominal_mm) ** 2 * floor_len
                if removed < floor or removed > ceiling:
                    return self._result(
                        False,
                        f"create_hole_wizard: feature '{getattr(feat, 'Name', '?')}' was created but removed "
                        f"{removed:.1f} mm³, which does not fit a {size} {hole_type} "
                        f"(expected {floor:.0f} <= V <= {ceiling:.0f} mm³). Check the selected face and size; "
                        "undo the feature with manage_feature delete if it is wrong.",
                        SwErrors.swFeatureError,
                        {"feature_name": getattr(feat, "Name", None), "volume_removed_mm3": round(removed, 2)},
                    )

            feat_name = getattr(feat, "Name", "HoleWizard")
            return self._result(
                True,
                f"Successfully created Hole Wizard feature ({hole_type} {size})"
                + (f", removed {removed:.1f} mm³" if removed is not None else ""),
                SwErrors.swSuccess,
                {"feature_name": feat_name, "hole_type": hole_type, "size": size,
                 "volume_removed_mm3": round(removed, 2) if removed is not None else None,
                 "note": "fastener_type is informational; the fastener is chosen from standard + hole_type."},
            )
        except Exception as e:
            logger.error(f"create_hole_wizard error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"create_hole_wizard error: {e}", SwErrors.swGeneralError)

    def hole_linear(
        self,
        face_axis: str,
        face_value: float,
        ref1_axis: str,
        ref1_value: float,
        dist1: float,
        ref2_axis: str,
        ref2_value: float,
        dist2: float,
        diameter: float = 5.5,
        depth: float = 0.0,
        tapped: bool = False,
        cbore_diameter: float = 0.0,
        cbore_depth: float = 0.0,
        unit: str = "mm",
    ) -> Dict:
        """
        Place a hole on a planar face at (face_axis == face_value) positioned parametrically
        at dist1 from ref1_axis==ref1_value and dist2 from ref2_axis==ref2_value.
        Verifies via volume delta that material was actually removed, flipping cut direction if needed.
        Matches lminventor's hole_linear tool.
        """
        try:
            from ..core.post_conditions import capture_vol_state, format_vol_delta

            doc, err = self.get_active_doc()
            if err:
                return err

            if tapped:
                return self._result(
                    False,
                    "hole_linear does not model threads (tapped=true was previously ignored silently). "
                    "Use create_hole_wizard with hole_type='tap' for a tapped hole.",
                    SwErrors.swInvalidParameter,
                )
            want_cbore = float(cbore_diameter or 0.0) > 0.0
            if want_cbore and (float(cbore_diameter) <= float(diameter) or float(cbore_depth or 0.0) <= 0.0):
                return self._result(
                    False,
                    "Counterbore needs cbore_diameter > diameter and cbore_depth > 0.",
                    SwErrors.swInvalidParameter,
                )

            bodies = get_attr_or_call(doc, "GetBodies2", 0, False) or []
            if not bodies:
                return self._result(False, "No solid bodies in active document.", SwErrors.swInvalidCall)

            body = bodies[0]
            faces = get_attr_or_call(body, "GetFaces") or []
            axis_idx_map = {"x": 0, "y": 1, "z": 2}
            fa = (face_axis or "").strip().lower()
            r1a = (ref1_axis or "").strip().lower()
            r2a = (ref2_axis or "").strip().lower()
            if fa not in axis_idx_map or r1a not in axis_idx_map or r2a not in axis_idx_map:
                return self._result(False, "Axes must be 'x', 'y', or 'z'.", SwErrors.swInvalidParameter)

            fa_i = axis_idx_map[fa]
            r1_i = axis_idx_map[r1a]
            r2_i = axis_idx_map[r2a]

            # Find largest planar face at face_axis == face_value (mm)
            target_face = None
            best_area = -1.0
            face_centroid_mm = [0.0, 0.0, 0.0]
            for f in faces:
                try:
                    surf = get_attr_or_call(f, "GetSurface")
                    if surf and not get_attr_or_call(surf, "IsPlane"):
                        continue
                    box = get_attr_or_call(f, "GetBox")
                    if not box or len(box) < 6:
                        continue
                    c_mm = [(box[0] + box[3]) * 500.0, (box[1] + box[4]) * 500.0, (box[2] + box[5]) * 500.0]
                    span_mm = abs(box[fa_i + 3] - box[fa_i]) * 1000.0
                    if span_mm < 1.0 and abs(c_mm[fa_i] - float(face_value)) <= 1.0:
                        area = float(get_attr_or_call(f, "GetArea") or 0.0)
                        if area > best_area:
                            best_area = area
                            target_face = f
                            face_centroid_mm = c_mm
                except Exception:
                    continue

            if target_face is None:
                return self._result(
                    False,
                    f"No planar face found at {face_axis}={face_value}mm",
                    SwErrors.swSelectionError,
                )

            # Compute target 3D hole center in mm by stepping inward from ref1 and ref2 edges toward centroid
            hole_pt_mm = list(face_centroid_mm)
            hole_pt_mm[fa_i] = float(face_value)
            sgn1 = 1.0 if face_centroid_mm[r1_i] >= float(ref1_value) else -1.0
            hole_pt_mm[r1_i] = float(ref1_value) + sgn1 * float(dist1)
            sgn2 = 1.0 if face_centroid_mm[r2_i] >= float(ref2_value) else -1.0
            hole_pt_mm[r2_i] = float(ref2_value) + sgn2 * float(dist2)

            vol_before = capture_vol_state(doc)
            v0 = vol_before[0] if vol_before else 0.0

            # Select target face, open sketch, draw circle at hole center, and cut-extrude (auto-resolving direction)
            doc.ClearSelection2(True)
            target_face.Select2(False, 0)
            doc.InsertSketch2(True)

            # Transform 3D model point (m) into active sketch 2D space using sketch.ModelToSketchTransform
            sk = getattr(doc.SketchManager, "ActiveSketch", None)
            sx_m, sy_m = hole_pt_mm[0] / 1000.0, hole_pt_mm[1] / 1000.0
            xform_ok = False
            try:
                if sk is not None:
                    xform = get_attr_or_call(sk, "ModelToSketchTransform")
                    xf_arr = get_attr_or_call(xform, "ArrayData") if xform is not None else None
                    if xf_arr and len(xf_arr) >= 12:
                        # Applied in Python: IMathUtility.CreatePoint fails under late binding.
                        from ..core.com_compat import transform_point
                        sp = transform_point(xf_arr, [c / 1000.0 for c in hole_pt_mm])
                        sx_m, sy_m = float(sp[0]), float(sp[1])
                        xform_ok = True
            except Exception as xf_ex:
                logger.debug(f"hole_linear sketch transform failed: {xf_ex}")
            if not xform_ok and fa_i != 2:
                doc.InsertSketch2(True)
                return self._result(
                    False,
                    "hole_linear: could not map the hole centre into the face's sketch coordinates.",
                    SwErrors.swGeneralError,
                )

            r_m = (float(diameter) / 2.0) / 1000.0
            from ..core.sketch_guard import arc_center_radius, close, direct_sketch, TOL_M
            with direct_sketch(doc):  # no snapping onto existing concentric edges (MCP-013)
                circle = doc.SketchManager.CreateCircleByRadius(sx_m, sy_m, 0.0, r_m)
            got = arc_center_radius(circle) if circle is not None else None
            if circle is None or (got is not None and not (close(got[0], (sx_m, sy_m)) and abs(got[1] - r_m) <= TOL_M)):
                doc.InsertSketch2(True)
                return self._result(
                    False,
                    "hole_linear: the hole circle could not be created exactly"
                    + (f" (got r={got[1] * 1000:.4f} mm at sketch ({got[0][0] * 1000:.4f}, {got[0][1] * 1000:.4f}))" if got else "")
                    + ". Nothing was cut; delete the leftover sketch with manage_feature if needed.",
                    SwErrors.swSketchError,
                )
            doc.InsertSketch2(True)

            # Through-all goes through the whole part along the hole axis on both sides of the face,
            # so it cannot stop after the first wall or depend on which side SolidWorks picks (MCP-012).
            through_all = (float(depth) <= 0.0)
            cut_res = self.cut_extrude(depth=float(depth) if not through_all else 10.0, through_all=through_all,
                                       both_directions=through_all, unit="mm")
            vol_after = capture_vol_state(doc)
            v1 = vol_after[0] if vol_after else v0

            if not cut_res.get("success") or (v0 > 0 and v1 >= v0 - 0.5):
                return self._result(
                    False,
                    f"hole_linear cut removed no material at { tuple(round(c, 2) for c in hole_pt_mm) }",
                    SwErrors.swFeatureError,
                    {"hole_center_mm": hole_pt_mm, "cut_result": cut_res},
                )

            # Measure the hole that was actually cut: cylinder faces whose axis is the hole axis.
            walls = self._hole_walls_on_axis(doc, hole_pt_mm, fa_i)
            radii = sorted({round(r, 4) for r, _ in walls})
            req_r_mm = float(diameter) / 2.0
            if walls and not any(abs(r - req_r_mm) <= 1e-3 for r, _ in walls):
                return self._result(
                    False,
                    f"hole_linear: requested D={diameter} mm but the hole on this axis has radius {radii} mm "
                    "(it merged with existing geometry). The cut feature is still in the tree; delete it with "
                    "manage_feature if it is wrong.",
                    SwErrors.swFeatureError,
                    {"hole_center_mm": hole_pt_mm, "measured_radii_mm": radii, "cut_result": cut_res},
                )
            walls_pierced = sum(1 for r, _ in walls if abs(r - req_r_mm) <= 1e-3)

            cbore_res = None
            if want_cbore:
                cbore_res = self.hole_linear(
                    face_axis=face_axis, face_value=face_value,
                    ref1_axis=ref1_axis, ref1_value=ref1_value, dist1=dist1,
                    ref2_axis=ref2_axis, ref2_value=ref2_value, dist2=dist2,
                    diameter=float(cbore_diameter), depth=float(cbore_depth),
                    tapped=False, cbore_diameter=0.0, cbore_depth=0.0, unit=unit,
                )
                if not cbore_res.get("success"):
                    return self._result(
                        False,
                        f"hole_linear: the hole was cut but the counterbore failed: {cbore_res.get('message')}",
                        SwErrors.swFeatureError,
                        {"hole_center_mm": hole_pt_mm, "cbore_result": cbore_res},
                    )
                vol_after = capture_vol_state(doc)

            suffix, delta_dict = format_vol_delta(vol_before, vol_after)
            extent_str = (f"through-all ({walls_pierced} wall(s) pierced)" if through_all else f"{depth}mm deep")
            cbore_str = f" with C'bore D={cbore_diameter}mm x {cbore_depth}mm" if want_cbore else ""
            return self._result(
                True,
                f"Hole D={diameter}mm {extent_str}{cbore_str} on {face_axis}={face_value} "
                f"({dist1}mm from {ref1_axis}={ref1_value}, {dist2}mm from {ref2_axis}={ref2_value}){suffix}",
                SwErrors.swSuccess,
                {
                    "hole_center_mm": [round(c, 2) for c in hole_pt_mm],
                    "diameter_mm": diameter,
                    "depth_mm": depth,
                    "through_all": through_all,
                    "cbore_diameter_mm": cbore_diameter if want_cbore else None,
                    "cbore_depth_mm": cbore_depth if want_cbore else None,
                    "walls_pierced": walls_pierced,
                    "geometry_delta": delta_dict,
                },
            )
        except Exception as e:
            logger.error(f"hole_linear error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"hole_linear error: {e}", SwErrors.swGeneralError)

    def _hole_walls_on_axis(self, doc, center_mm, axis_i: int):
        """(radius_mm, area_mm2) of every cylindrical face whose axis is the line through center_mm along axis_i."""
        import math as _m
        axis = [0.0, 0.0, 0.0]
        axis[axis_i] = 1.0
        c = [v / 1000.0 for v in center_mm]
        out = []
        for body in (get_attr_or_call(doc, "GetBodies2", 0, False) or []):
            for f in (get_attr_or_call(body, "GetFaces") or []):
                try:
                    surf = get_attr_or_call(f, "GetSurface")
                    if surf is None or not bool(get_attr_or_call(surf, "IsCylinder")):
                        continue
                    p = [float(v) for v in get_attr_or_call(surf, "CylinderParams")]
                    o, d, r = p[0:3], p[3:6], p[6]
                    if abs(abs(sum(a * b for a, b in zip(d, axis))) - 1.0) > 1e-6:
                        continue  # not parallel to the hole axis
                    v = [c[i] - o[i] for i in range(3)]
                    along = sum(v[i] * axis[i] for i in range(3))
                    perp = _m.sqrt(max(0.0, sum(x * x for x in v) - along * along))
                    if perp > 1e-6:
                        continue  # parallel but off-axis
                    out.append((r * 1000.0, float(get_attr_or_call(f, "GetArea") or 0.0) * 1e6))
                except Exception:
                    continue
        return out

    # ========================================================================
    # Swept tube along a 3D path (refrigerant lines, drain tubes, harness bundles)
    # ========================================================================

    def sweep_tube(self, points: List[List[float]], diameter: float, bend_radius: float = 0.0,
                   merge: bool = True, unit: str = "mm") -> Dict:
        """
        Solid round bar/tube envelope of `diameter` swept along a 3D polyline through `points`
        (world [x, y, z]), with an optional `bend_radius` at every corner. Built as a 3D sketch
        plus a circular-profile sweep; the volume is checked against pi r^2 x path length.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            if self.get_document_type(doc) != "Part":
                return self._result(False, "sweep_tube needs a Part document.", SwErrors.swInvalidDocumentType)
            if not points or len(points) < 2 or any(len(p) != 3 for p in points):
                return self._result(False, "points must be at least two [x, y, z] points.", SwErrors.swInvalidInput)
            if float(diameter) <= 0 or float(bend_radius or 0) < 0:
                return self._result(False, "diameter must be > 0 and bend_radius >= 0.", SwErrors.swInvalidInput)
            if get_attr_or_call(doc, "GetActiveSketch2") is not None:
                return self._result(False, "Close the active sketch first (close_sketch).", SwErrors.swSketchError)
            pts = [tuple(self._units.to_meters(float(c), unit) for c in p) for p in points]
            d_m = self._units.to_meters(float(diameter), unit)
            r_b = self._units.to_meters(float(bend_radius or 0.0), unit)
            segs = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
            if min(segs) < 1e-6:
                return self._result(False, "Two consecutive points are identical.", SwErrors.swInvalidInput)
            # path length with filleted corners, and a check that every bend fits its two legs
            length = sum(segs)
            if r_b > 0:
                if r_b < d_m / 2.0:
                    return self._result(False, "bend_radius (centre line) must be at least diameter / 2.",
                                        SwErrors.swInvalidInput)
                used = [0.0] * len(segs)
                for i in range(1, len(pts) - 1):
                    u = [(pts[i - 1][k] - pts[i][k]) / segs[i - 1] for k in range(3)]
                    v = [(pts[i + 1][k] - pts[i][k]) / segs[i] for k in range(3)]
                    cosang = max(-1.0, min(1.0, sum(a * b for a, b in zip(u, v))))
                    ang = math.acos(cosang)          # angle between the legs at the corner
                    if abs(ang - math.pi) < 1e-6:
                        continue                      # straight through: no bend
                    if ang < 1e-3:
                        return self._result(False, f"The path folds back on itself at point {i + 1}.",
                                            SwErrors.swInvalidInput)
                    tan_len = r_b / math.tan(ang / 2.0)
                    used[i - 1] += tan_len
                    used[i] += tan_len
                    length += r_b * (math.pi - ang) - 2.0 * tan_len
                short = [i + 1 for i, (u_, s_) in enumerate(zip(used, segs)) if u_ > s_ + 1e-9]
                if short:
                    return self._result(
                        False, f"bend_radius {bend_radius} does not fit segment(s) {short} (too short for the bends "
                               "at their ends). Use a smaller bend_radius or longer segments.", SwErrors.swInvalidInput)

            pre_state = capture_pre_mutation_state(doc)
            vol0 = None
            try:
                vol0 = float(get_attr_or_call(doc, "GetMassProperties")[3])
            except Exception:
                vol0 = 0.0
            sm = doc.SketchManager
            sm.Insert3DSketch(True)
            sketch_feat = None
            try:
                sm.AddToDB = True
                ok_lines = all(sm.CreateLine(a[0], a[1], a[2], b[0], b[1], b[2]) is not None
                               for a, b in zip(pts, pts[1:]))
                sm.AddToDB = False
                bad_corner = None
                if ok_lines and r_b > 0:
                    for i in range(1, len(pts) - 1):
                        u = [(pts[i - 1][k] - pts[i][k]) / segs[i - 1] for k in range(3)]
                        v = [(pts[i + 1][k] - pts[i][k]) / segs[i] for k in range(3)]
                        if abs(sum(a * b for a, b in zip(u, v)) + 1.0) < 1e-9:
                            continue
                        doc.ClearSelection2(True)
                        doc.Extension.SelectByID2("", "SKETCHPOINT", pts[i][0], pts[i][1], pts[i][2], False, 0,
                                                  null_dispatch(), 0)
                        if sm.CreateFillet(r_b, 1) is None:
                            bad_corner = i + 1
                            break
            finally:
                try:
                    sm.AddToDB = False
                except Exception:
                    pass
                sm.Insert3DSketch(True)
            f_it = get_attr_or_call(doc, "FirstFeature")
            while f_it is not None:
                if get_attr_or_call(f_it, "GetTypeName2") == "3DProfileFeature":
                    sketch_feat = f_it
                f_it = get_attr_or_call(f_it, "GetNextFeature")

            def drop_sketch():
                try:
                    if sketch_feat is not None:
                        doc.ClearSelection2(True)
                        sketch_feat.Select2(False, 0)
                        doc.Extension.DeleteSelection2(0)
                except Exception:
                    pass

            if not ok_lines or bad_corner or sketch_feat is None:
                drop_sketch()
                return self._result(
                    False,
                    "sweep_tube: the 3D path could not be drawn"
                    + (f" (the bend at point {bad_corner} could not be filleted)" if bad_corner else "")
                    + ". Nothing was left in the part.", SwErrors.swSketchError)
            fm = doc.FeatureManager
            sd = fm.CreateDefinition(17)  # swFmSweep
            sd.CircularProfile = True
            sd.CircularProfileDiameter = d_m
            try:
                sd.Merge = bool(merge)
            except Exception:
                pass
            sd.Path = sketch_feat
            feat = fm.CreateFeature(sd)
            get_attr_or_call(doc, "EditRebuild3")
            if not feat:
                drop_sketch()
                return self._result(
                    False, "sweep_tube: SolidWorks could not sweep along this path (the tube would intersect itself: "
                           "use a larger bend_radius or a smaller diameter). Nothing was left in the part.",
                    SwErrors.swFeatureError)
            ok, post_msg, post_diag = verify_mutation_post_condition(doc, feat, pre_state, "feature")
            if not ok:
                return self._result(False, f"sweep_tube failed post-condition verification: {post_msg}",
                                    SwErrors.swFeatureError, post_diag)
            expected = math.pi * (d_m / 2.0) ** 2 * length * 1e9
            added = None
            try:
                added = (float(get_attr_or_call(doc, "GetMassProperties")[3]) - (vol0 or 0.0)) * 1e9
            except Exception:
                pass
            note = ""
            if added is not None and abs(added - expected) > max(0.02 * expected, 1.0):
                note = (f" NOTE: it added {added:.1f} mm3 but a free tube of this path is {expected:.1f} mm3: it "
                        "overlaps or merged with existing geometry.")
            return self._result(
                True,
                f"Swept tube D={diameter}{unit} along {len(pts)} points, centre-line length "
                f"{length * 1000.0:.2f} mm, bend radius {bend_radius}{unit}: {feat.Name}.{note}",
                SwErrors.swSuccess,
                {"feature_name": feat.Name, "path_sketch": str(sketch_feat.Name),
                 "length_mm": round(length * 1000.0, 3), "expected_volume_mm3": round(expected, 2),
                 "added_volume_mm3": round(added, 2) if added is not None else None, "merged": bool(merge)})
        except Exception as e:
            logger.error(f"sweep_tube error: {e}\n{traceback.format_exc()}")
            try:
                doc_now, _ = self.get_active_doc()
                if doc_now is not None and get_attr_or_call(doc_now, "GetActiveSketch2") is not None:
                    doc_now.SketchManager.Insert3DSketch(True)
            except Exception:
                pass
            return self._result(False, f"sweep_tube error: {e}", SwErrors.swGeneralError)

    # ========================================================================
    # Multi-body parts: list and combine solid bodies
    # ========================================================================

    def _solid_bodies(self, doc):
        """[(index, body, name, box_mm, volume_mm3)] for every solid body of the active part."""
        out = []
        for i, b in enumerate(doc.GetBodies2(0, False) or (), start=1):
            box = None
            vol = None
            try:
                bx = get_attr_or_call(b, "GetBodyBox")
                box = {"min": [round(float(v) * 1000.0, 3) for v in bx[:3]],
                       "max": [round(float(v) * 1000.0, 3) for v in bx[3:6]]}
            except Exception:
                pass
            try:
                mp = b.GetMassProperties(1.0)
                vol = round(float(mp[3]) * 1e9, 2)
            except Exception:
                pass
            out.append((i, b, str(b.Name), box, vol))
        return out

    def list_bodies(self) -> Dict:
        """One line per solid body: index, name, bounding box, volume."""
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            if self.get_document_type(doc) != "Part":
                return self._result(False, "Bodies can be listed in a Part document only.", SwErrors.swInvalidDocumentType)
            rows = self._solid_bodies(doc)
            lines = []
            for i, _b, name, box, vol in rows:
                span = (f"X {box['min'][0]:g}..{box['max'][0]:g} Y {box['min'][1]:g}..{box['max'][1]:g} "
                        f"Z {box['min'][2]:g}..{box['max'][2]:g}") if box else "box ?"
                lines.append(f"[{i}] {name}  {span}  V={vol if vol is not None else '?'} mm3")
            return self._result(True, (f"{len(rows)} solid bod{'y' if len(rows) == 1 else 'ies'}:\n" + "\n".join(lines))
                                if rows else "No solid bodies.", SwErrors.swSuccess, {"count": len(rows)})
        except Exception as e:
            return self._result(False, f"list_bodies error: {e}", SwErrors.swGeneralError)

    def combine_bodies(self, operation: str = "add", main_body: Optional[int] = None,
                       tool_bodies: Optional[List[int]] = None) -> Dict:
        """
        Boolean of solid bodies in a multi-body part (Insert > Features > Combine).
        add: union of tool_bodies (and main_body); subtract: main_body minus tool_bodies (the tool
        bodies are consumed); intersect: common volume of main_body and tool_bodies.
        Bodies are 1-based indices from list_topology kind='bodies'.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            if self.get_document_type(doc) != "Part":
                return self._result(False, "combine_bodies needs a Part document.", SwErrors.swInvalidDocumentType)
            op = str(operation or "").strip().lower()
            op_code = {"add": 15903, "subtract": 15902, "intersect": 15901}.get(op)
            if op_code is None:
                return self._result(False, "operation must be add, subtract or intersect.", SwErrors.swInvalidInput)
            rows = self._solid_bodies(doc)
            by_idx = {r[0]: r[1] for r in rows}
            tools = [int(t) for t in (tool_bodies or [])]
            ids = tools + ([int(main_body)] if main_body is not None else [])
            bad = [i for i in ids if i not in by_idx]
            if bad or len(set(ids)) != len(ids):
                return self._result(False, f"Body index/indices {bad or ids} not valid or repeated; the part has "
                                           f"{len(rows)} solid bodies (list_topology kind='bodies').", SwErrors.swInvalidInput)
            if op_code == 15902 and (main_body is None or not tools):
                return self._result(False, "subtract needs main_body and at least one tool body.", SwErrors.swInvalidInput)
            if op_code != 15902 and len(ids) < 2:
                return self._result(False, f"{op} needs at least two bodies.", SwErrors.swInvalidInput)

            pre_state = capture_pre_mutation_state(doc)
            before = {r[2]: r[4] for r in rows}
            V = win32com.client.VARIANT
            if op_code == 15902:
                feat = doc.FeatureManager.InsertCombineFeature(
                    op_code, by_idx[int(main_body)],
                    V(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [by_idx[t] for t in tools]))
            else:
                feat = doc.FeatureManager.InsertCombineFeature(
                    op_code, null_dispatch(),
                    V(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [by_idx[i] for i in ids]))
            get_attr_or_call(doc, "EditRebuild3")
            if not feat:
                return self._result(
                    False,
                    f"Combine ({op}) was not created. For add the bodies must touch or overlap; for subtract and "
                    "intersect the bodies must overlap.", SwErrors.swFeatureError)
            ok, post_msg, post_diag = verify_mutation_post_condition(doc, feat, pre_state, "feature")
            if not ok:
                return self._result(False, f"Combine failed post-condition verification: {post_msg}",
                                    SwErrors.swFeatureError, post_diag)
            after = self._solid_bodies(doc)
            lines = [f"[{r[0]}] {r[2]} V={r[4]} mm3" for r in after]
            return self._result(
                True,
                f"Combine ({op}) '{feat.Name}': {len(rows)} -> {len(after)} bodies. Now: " + "; ".join(lines),
                SwErrors.swSuccess,
                {"feature_name": feat.Name, "operation": op, "bodies_before": len(rows), "bodies_after": len(after),
                 "volumes_before_mm3": before})
        except Exception as e:
            logger.error(f"combine_bodies error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"combine_bodies error: {e}", SwErrors.swGeneralError)

    # ========================================================================
    # Global Variables & Equations (IEquationMgr + Model Dimensions)
    # ========================================================================

    def get_parameters(self) -> Dict[str, Any]:
        """
        Get all Global Variables, Equations (IEquationMgr), and Model Dimensions in the active document.
        Matches lminventor's get_parameters tool.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            equations = []
            try:
                eq_mgr = get_attr_or_call(doc, "GetEquationMgr")
                if eq_mgr is not None:
                    cnt = int(get_attr_or_call(eq_mgr, "GetCount") or 0)
                    for i in range(cnt):
                        try:
                            eq_str = str(eq_mgr.Equation(i) or "")
                            val = eq_mgr.Value(i)
                            is_glob = bool(eq_mgr.GlobalVariable(i))
                            supp = bool(eq_mgr.Suppression(i))
                            name = eq_str.split("=")[0].strip().strip('"') if "=" in eq_str else f"Eq{i}"
                            expr = eq_str.split("=", 1)[1].strip() if "=" in eq_str else eq_str
                            equations.append({
                                "index": i,
                                "name": name,
                                "expression": expr,
                                "equation": eq_str,
                                "value": val,
                                "global_variable": is_glob,
                                "suppressed": supp,
                            })
                        except Exception:
                            pass
            except Exception as eq_ex:
                logger.debug(f"GetEquationMgr warning: {eq_ex}")

            dims_res = self.get_dimensions()
            dims_list = dims_res.get("data", {}).get("dimensions", []) if dims_res.get("success") else []

            return self._result(
                True,
                f"Retrieved {len(equations)} equation(s)/global variable(s) and {len(dims_list)} model dimension(s)",
                SwErrors.swSuccess,
                {
                    "equations": equations,
                    "dimensions": dims_list,
                    "equation_count": len(equations),
                    "dimension_count": len(dims_list),
                },
            )
        except Exception as e:
            logger.error(f"get_parameters error: {e}")
            return self._result(False, f"get_parameters error: {e}", SwErrors.swGeneralError)

    def add_parameter(self, name: str, expression: str, units: str = "mm") -> Dict[str, Any]:
        """
        Add a new Global Variable or Equation to SolidWorks IEquationMgr.
        Matches lminventor's add_parameter tool.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            eq_mgr = get_attr_or_call(doc, "GetEquationMgr")
            if eq_mgr is None:
                return self._result(False, "EquationMgr is not available on the active document.", SwErrors.swInvalidCall)

            clean_name = name.strip().strip('"')
            clean_expr = str(expression).strip()
            # Format: '"Width" = 100mm' or '"D1@Sketch1" = "Width" * 2'
            eq_text = f'"{clean_name}" = {clean_expr}'
            idx = eq_mgr.Add2(-1, eq_text, True)
            if idx < 0:
                return self._result(
                    False,
                    f"Failed to add parameter/equation '{eq_text}' (EquationMgr.Add2 returned {idx})",
                    SwErrors.swInvalidParameter,
                )

            get_attr_or_call(doc, "EditRebuild3")
            val = None
            try:
                val = eq_mgr.Value(idx)
            except Exception:
                pass

            return self._result(
                True,
                f"User parameter '{clean_name}' = '{clean_expr}' added (index={idx}, value={val})",
                SwErrors.swSuccess,
                {"index": idx, "name": clean_name, "expression": clean_expr, "equation": eq_text, "value": val},
            )
        except Exception as e:
            logger.error(f"add_parameter error: {e}")
            return self._result(False, f"add_parameter error: {e}", SwErrors.swGeneralError)

    def set_parameter(self, name: str, expression: str, units: str = "mm") -> Dict[str, Any]:
        """
        Update an existing Global Variable / Equation in IEquationMgr, or fall back to setting a model dimension.
        Matches lminventor's set_parameter tool.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            clean_name = name.strip().strip('"')
            clean_expr = str(expression).strip()

            eq_mgr = get_attr_or_call(doc, "GetEquationMgr")
            if eq_mgr is not None:
                cnt = int(get_attr_or_call(eq_mgr, "GetCount") or 0)
                for i in range(cnt):
                    try:
                        eq_str = str(eq_mgr.Equation(i) or "")
                        lhs = eq_str.split("=")[0].strip().strip('"') if "=" in eq_str else ""
                        if lhs.lower() == clean_name.lower():
                            new_eq = f'"{lhs}" = {clean_expr}'

                            def _norm(s: str) -> str:
                                return "".join(str(s).split()).lower()

                            # Try the setters in order until the stored equation text really changes;
                            # SetEquationAndConfigurationOption silently no-ops under late binding.
                            setters = (
                                lambda: eq_mgr.SetEquationAndConfigurationOption(i, new_eq, 1, None),
                                lambda: eq_mgr.SetEquationAndConfigurationOption(
                                    i, new_eq, 1, win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_BSTR, [])
                                ),
                                lambda: setattr(eq_mgr, "Equation", (i, new_eq)),
                                lambda: eq_mgr.SetEquation(i, new_eq) if hasattr(eq_mgr, "SetEquation") else None,
                            )
                            applied = False
                            for setter in setters:
                                try:
                                    setter()
                                except Exception as set_err:
                                    logger.debug(f"set_parameter setter failed: {set_err}")
                                if _norm(eq_mgr.Equation(i) or "") == _norm(new_eq):
                                    applied = True
                                    break
                            if not applied:
                                # Last resort: replace the equation in place (delete + re-add at same index).
                                try:
                                    eq_mgr.Delete(i)
                                    eq_mgr.Add2(i, new_eq, True)
                                    applied = _norm(eq_mgr.Equation(i) or "") == _norm(new_eq)
                                except Exception as readd_err:
                                    logger.debug(f"set_parameter delete/re-add failed: {readd_err}")

                            get_attr_or_call(doc, "EditRebuild3")
                            val = eq_mgr.Value(i)
                            if not applied:
                                return self._result(
                                    False,
                                    f"Parameter '{lhs}' could not be updated: equation is still "
                                    f"'{eq_mgr.Equation(i)}' (value={val}).",
                                    SwErrors.swGeneralError,
                                    {"index": i, "name": lhs, "expression": str(eq_mgr.Equation(i)), "value": val},
                                )
                            return self._result(
                                True,
                                f"Parameter '{lhs}' set to '{clean_expr}' (value={val})",
                                SwErrors.swSuccess,
                                {"index": i, "name": lhs, "expression": clean_expr, "value": val},
                            )
                    except Exception as loop_err:
                        logger.debug(f"set_parameter equation scan warning at {i}: {loop_err}")

            # Fallback: try setting numeric model dimension via set_dimension
            try:
                num_val = float(clean_expr.replace("mm", "").replace("deg", "").strip())
                return self.set_dimension(name=clean_name, value=num_val, units=units)
            except ValueError:
                # Expression is a formula on a dimension not yet in EquationMgr -> add equation
                return self.add_parameter(name=clean_name, expression=clean_expr, units=units)
        except Exception as e:
            logger.error(f"set_parameter error: {e}")
            return self._result(False, f"set_parameter error: {e}", SwErrors.swGeneralError)

