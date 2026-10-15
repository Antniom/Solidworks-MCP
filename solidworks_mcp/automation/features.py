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
    
    def _find_last_sketch(self, doc) -> Optional[str]:
        """
        Find the name of the last sketch in the feature tree.
        Uses get_attr_or_call for safe COM member resolution.
        
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
                       reverse_dir: bool = False, unit: str = None) -> Dict:
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
            
            # Step 2: Determine end condition
            end_cond = 6 if both_directions else 0  # 6=MidPlane, 0=Blind
            
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
                    False,          # Dchk1 - draft on/off
                    False,          # Dchk2 - draft on/off 2
                    False,          # Ddir1 - draft outward
                    False,          # Ddir2 - draft outward 2
                    0.0,            # Dang1 - draft angle (radians)
                    0.0,            # Dang2 - draft angle 2
                    False,          # OffsetReverse1
                    False,          # OffsetReverse2
                    False,          # TranslateSurface1
                    False,          # TranslateSurface2
                    True,           # Merge - merge result
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
                        False, False, False, False,
                        0.0, 0.0,
                        False, False, False, False,
                        True, True, True,
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
                    both_directions: bool = False, unit: str = None) -> Dict:
        """
        Cut extrude (remove material)
        FIXED v4.0: Proper sketch handling and parameter counts
        
        Args:
            depth: Cut depth (ignored if through_all=True)
            through_all: Cut through entire model
            both_directions: Cut in both directions
            unit: Unit for depth
        
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
            
            # Determine end condition
            if through_all:
                if both_directions:
                    end_cond = 2  # swEndCondThroughAllBoth
                else:
                    end_cond = 1  # swEndCondThroughAll
                cut_depth = 0
            else:
                if both_directions:
                    end_cond = 6  # swEndCondMidPlane
                else:
                    end_cond = 0  # swEndCondBlind
                cut_depth = depth_m
            
            feat = None
            method_used = ""
            
            # Method 1: FeatureCut4 (27 params for SW 2025)
            for dir_opt in [False, True]:
                try:
                    feat = doc.FeatureManager.FeatureCut4(
                        True,           # Sd - single direction
                        False,          # Flip
                        dir_opt,        # Dir - direction (try normal, then opposite)
                        end_cond,       # T1 - end condition
                        0,              # T2 - end condition 2
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
                for dir_opt in [False, True]:
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
                return self._result(False,
                    f"Cut failed on sketch '{sketch_name}'. "
                    f"Ensure sketch is drawn on an existing face with a closed profile.",
                    SwErrors.swFeatureError,
                    {"sketch_name": sketch_name})
            
            # Post-condition verification (F3)
            ok, post_msg, post_diag = verify_mutation_post_condition(doc, feat, pre_state, "feature")
            if not ok:
                return self._result(False, f"Cut extrude failed post-condition verification: {post_msg}",
                                  SwErrors.swFeatureError, post_diag)
            
            cut_type = "through all" if through_all else f"{depth}{unit_str}"
            
            return self._result(True, f"Cut extrude: {cut_type} [{method_used}]",
                              SwErrors.swSuccess,
                              {"depth": depth, "through_all": through_all,
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
                except:
                    pass
                
                # FIXED: GetNextFeature is a property in SW 2025
                try:
                    feat = feat.GetNextFeature
                except:
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

                    # Value in document units
                    if dim_type_code in (1, 3, 16):  # Angular
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
            if action_clean not in ("suppress", "unsuppress", "rename", "delete", "reorder", "set_color"):
                return self._result(
                    False,
                    f"Invalid action '{action}'. Valid actions: 'suppress', 'unsuppress', 'rename', 'delete', 'reorder', 'set_color'.",
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
    ) -> Dict:
        """
        Create a shell feature on active part (IModelDoc2.InsertFeatureShell).
        Uses currently selected face(s) as faces to remove (or open cavity).
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
                return self._result(False, f"create_shell failed post-condition verification: {post_msg}", SwErrors.swFeatureError, post_diag)

            feat_name = getattr(feat, "Name", "Shell1") if feat else "Shell1"
            return self._result(
                True,
                f"Successfully created shell (thickness={thickness}{unit})",
                SwErrors.swSuccess,
                {"feature_name": feat_name, "thickness": thickness, "unit": unit},
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
    ) -> Dict:
        """
        Create a linear or circular pattern feature (IFeatureManager.FeatureLinearPattern4 / FeatureCircularPattern4).
        """
        import math
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            ptype = str(pattern_type).strip().lower()
            pre_state = capture_pre_mutation_state(doc)

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
                                    if crv:
                                        is_ln = getattr(crv, "IsLine", lambda: False)() or getattr(crv, "Identity", lambda: 0)() == 3001
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

                if feature_name:
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
                        faces = get_attr_or_call(b, "GetFaces") or ()
                        for f in faces:
                            try:
                                surf = get_attr_or_call(f, "GetSurface")
                                is_cyl = False
                                if surf:
                                    is_cyl = getattr(surf, "IsCylinder", lambda: False)() or getattr(surf, "Identity", lambda: 0)() == 4002
                                if is_cyl:
                                    f.Select2(True, 1)
                                    has_mark1 = True
                                    break
                            except Exception:
                                pass
                        if has_mark1:
                            break

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
            std_int = std_map.get(str(standard).lower(), 8)

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

            diam_m = self._units.to_meters(diameter, unit) if diameter else 0.006
            depth_m = self._units.to_meters(depth, unit) if depth else 0.02

            pre_state = capture_pre_mutation_state(doc)

            feat = doc.FeatureManager.HoleWizard5(
                htype_int,
                std_int,
                fastener_idx,
                str(size),
                end_int,
                diam_m,
                depth_m,
                depth_m,
                0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
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
                return self._result(False, f"create_hole_wizard failed post-condition verification: {post_msg}", SwErrors.swFeatureError, post_diag)

            feat_name = getattr(feat, "Name", "HoleWizard")
            return self._result(
                True,
                f"Successfully created Hole Wizard feature ({hole_type} {size})",
                SwErrors.swSuccess,
                {"feature_name": feat_name, "hole_type": hole_type, "size": size},
            )
        except Exception as e:
            logger.error(f"create_hole_wizard error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"create_hole_wizard error: {e}", SwErrors.swGeneralError)

