"""
Assembly automation operations for SolidWorks MCP.
Provides component insertion, mate creation, interference checking, and component querying.
"""

import os
import logging
from typing import Dict, List, Optional, Any

from ..constants import SwErrors, SwDocumentTypes, SwMateTypes
from ..core import get_fs_policy, capture_pre_mutation_state, verify_mutation_post_condition
from ..core.com_compat import get_attr_or_call, byref_int

try:
    import win32com.client
    import pythoncom
    HAS_PYWIN32 = True
except ImportError:
    HAS_PYWIN32 = False

logger = logging.getLogger("SolidWorksMCP.Assemblies")


class AssemblyOperations:
    """
    Mixin class providing assembly operations for SolidWorksAutomation.
    """

    MATE_TYPES = {
        "coincident": 0,    # swMateCOINCIDENT
        "concentric": 1,    # swMateCONCENTRIC
        "perpendicular": 2, # swMatePERPENDICULAR
        "parallel": 3,      # swMatePARALLEL
        "tangent": 4,       # swMateTANGENT
        "distance": 5,      # swMateDISTANCE
        "angle": 6,         # swMateANGLE
    }

    def add_component(
        self,
        component_path: str,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        unit: str = "mm",
    ) -> Dict:
        """
        Insert a component (part or subassembly) into the active assembly.
        Pre-loads the referenced document to ensure reliable AddComponent4 resolution.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            doc_type_active = get_attr_or_call(doc, "GetType")
            if doc_type_active != SwDocumentTypes.swDocASSEMBLY:
                return self._result(
                    False,
                    "Active document is not an assembly. Use create_new_assembly first.",
                    SwErrors.swInvalidDocumentType,
                )

            # Filesystem policy validation
            is_valid, canonical, err_msg = get_fs_policy().validate_path(component_path, must_exist=True)
            if not is_valid:
                return self._result(
                    False,
                    f"Filesystem security violation: {err_msg}",
                    SwErrors.swFileNotFoundError,
                )
            component_path = canonical

            # Convert translation to meters
            x_m = self._units.to_meters(x, unit)
            y_m = self._units.to_meters(y, unit)
            z_m = self._units.to_meters(z, unit)

            # Pre-load component document in background if not already loaded
            ext = os.path.splitext(component_path)[1].lower()
            doc_type = SwDocumentTypes.swDocASSEMBLY if ext == ".sldasm" else SwDocumentTypes.swDocPART

            if HAS_PYWIN32:
                errors = byref_int(0)
                warnings = byref_int(0)
                # OpenDoc6 with Option 1 = swOpenDocOptions_Silent
                self._sw_app.OpenDoc6(component_path, int(doc_type), 1, "", errors, warnings)
                # Ensure assembly remains active
                title = get_attr_or_call(doc, "GetTitle")
                self._sw_app.ActivateDoc3(title, False, 2, errors)

            # Capture pre-state for post-condition verification (F3)
            pre_state = capture_pre_mutation_state(doc)

            # Insert component
            try:
                comp = doc.AddComponent4(component_path, "", x_m, y_m, z_m)
            except Exception as insert_ex:
                logger.warning(f"AddComponent4 failed for '{component_path}': {insert_ex}")
                comp = None

            # Post-condition verification (F3)
            ok, post_msg, post_diag = verify_mutation_post_condition(doc, comp, pre_state, "component")
            if not ok:
                return self._result(
                    False,
                    f"Component insertion failed post-condition: {post_msg}",
                    SwErrors.swInvalidCall,
                    post_diag,
                )

            comp_name = comp.Name2
            return self._result(
                True,
                f"Component '{comp_name}' inserted into assembly",
                SwErrors.swSuccess,
                {
                    "name": comp_name,
                    "path": component_path,
                    "position_m": [x_m, y_m, z_m],
                    "unit": unit,
                },
            )
        except Exception as e:
            logger.error(f"add_component error: {e}")
            return self._result(False, f"Add component error: {e}", SwErrors.swGeneralError)

    def add_mate(
        self,
        mate_type: str,
        entity1: str,
        entity2: str,
        comp1: Optional[str] = None,
        comp2: Optional[str] = None,
        align: int = 0,
        flip: bool = False,
        distance: float = 0.0,
        angle: float = 0.0,
        unit: str = "mm",
    ) -> Dict:
        """
        Add a mate between two entities in the active assembly.

        Args:
            mate_type: 'coincident', 'concentric', 'perpendicular', 'parallel', 'tangent', 'distance', 'angle'
            entity1: Name of first entity/feature/plane (e.g. 'Top Plane' or 'Face<1>')
            entity2: Name of second entity/feature/plane
            comp1: Optional component name for entity1 (e.g. 'cube-1')
            comp2: Optional component name for entity2 (e.g. 'cube-2')
            align: 0 = swMateAlignALIGNED, 1 = swMateAlignANTI_ALIGNED, 2 = swMateAlignCLOSEST
            flip: Flip mate dimension direction
            distance: Distance value (if mate_type is 'distance')
            angle: Angle value in degrees (if mate_type is 'angle')
            unit: Unit for distance ('mm', 'inch', etc.)
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            doc_type_active = get_attr_or_call(doc, "GetType")
            if doc_type_active != SwDocumentTypes.swDocASSEMBLY:
                return self._result(
                    False,
                    "Active document is not an assembly.",
                    SwErrors.swInvalidDocumentType,
                )

            mate_type_lower = mate_type.lower().strip()
            if mate_type_lower not in self.MATE_TYPES:
                return self._result(
                    False,
                    f"Unknown mate type '{mate_type}'. Valid types: {list(self.MATE_TYPES.keys())}",
                    SwErrors.swInvalidParameter,
                )
            sw_mate_type = self.MATE_TYPES[mate_type_lower]

            # Clear selection
            doc.ClearSelection2(True)

            # Helper to resolve selection for component entity
            def select_target(ent_name: str, comp_name: Optional[str], append: bool) -> bool:
                if comp_name:
                    # Look up component object
                    comps = doc.GetComponents(False)
                    target_comp = None
                    if comps:
                        for c in comps:
                            if c.Name2.lower() == comp_name.lower():
                                target_comp = c
                                break
                    if target_comp is not None:
                        feat = target_comp.FeatureByName(ent_name)
                        if feat is not None:
                            return feat.Select2(append, 1)

                    # Try SelectByID2 with qualified name: "entity@component"
                    qualified = f"{ent_name}@{comp_name}"
                    null_callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None) if HAS_PYWIN32 else None
                    if doc.Extension.SelectByID2(qualified, "", 0.0, 0.0, 0.0, append, 1, null_callout, 0):
                        return True
                    if doc.Extension.SelectByID2(qualified, "PLANE", 0.0, 0.0, 0.0, append, 1, null_callout, 0):
                        return True
                    return False
                else:
                    null_callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None) if HAS_PYWIN32 else None
                    return doc.Extension.SelectByID2(ent_name, "", 0.0, 0.0, 0.0, append, 1, null_callout, 0)

            ok1 = select_target(entity1, comp1, False)
            if not ok1:
                return self._result(
                    False,
                    f"Failed to select first mate entity '{entity1}' (component: {comp1})",
                    SwErrors.swSelectionError,
                )

            ok2 = select_target(entity2, comp2, True)
            if not ok2:
                return self._result(
                    False,
                    f"Failed to select second mate entity '{entity2}' (component: {comp2})",
                    SwErrors.swSelectionError,
                )

            # Convert dimensional parameters
            dist_m = abs(self._units.to_meters(distance, unit))
            angle_rad = angle * 3.141592653589793 / 180.0

            if not HAS_PYWIN32:
                return self._result(False, "pywin32 not available", SwErrors.swGeneralError)

            # Capture pre-state for post-condition verification (F3)
            pre_state = capture_pre_mutation_state(doc)

            mate_err = byref_int(0)
            mate_feat = doc.AddMate3(
                sw_mate_type,
                int(align),
                bool(flip),
                dist_m,
                0.0,
                0.0,
                angle_rad,
                0.0,
                0.0,
                False,
                False,
                0,
                mate_err,
            )

            # Rebuild assembly
            try:
                doc.EditRebuild3
            except Exception:
                pass

            mate_error_messages = {
                0: "Unknown mate error (swAddMateError_ErrorUknown)",
                1: "NoError",
                2: "Incorrect mate type (swAddMateError_IncorrectMateType)",
                3: "Incorrect alignment (swAddMateError_IncorrectAlignment)",
                4: "Incorrect selections (swAddMateError_IncorrectSelections)",
                5: "Overdefined assembly (swAddMateError_OverDefinedAssembly)",
                6: "Incorrect gear ratios (swAddMateError_IncorrectGearRatios)",
            }

            err_val = getattr(mate_err, "value", 0)
            if mate_feat is None or err_val != 1:
                err_text = mate_error_messages.get(err_val, f"Error code {err_val}")
                return self._result(
                    False,
                    f"AddMate3 failed: {err_text}",
                    SwErrors.swInvalidCall,
                    {"mate_error_code": err_val},
                )

            # Post-condition verification (F3)
            ok, post_msg, post_diag = verify_mutation_post_condition(doc, mate_feat, pre_state, "mate")
            if not ok:
                return self._result(
                    False,
                    f"Mate failed post-condition verification: {post_msg}",
                    SwErrors.swInvalidCall,
                    post_diag,
                )

            mate_name = mate_feat.Name if hasattr(mate_feat, "Name") else "Mate"
            return self._result(
                True,
                f"Created {mate_type} mate '{mate_name}'",
                SwErrors.swSuccess,
                {
                    "mate_name": mate_name,
                    "mate_type": mate_type,
                    "align": align,
                    "flip": flip,
                },
            )
        except Exception as e:
            logger.error(f"add_mate error: {e}")
            return self._result(False, f"Add mate error: {e}", SwErrors.swGeneralError)

    def check_interference(self, treat_coincidence_as_interference: bool = False) -> Dict:
        """
        Check for geometric interference between components in the active assembly.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            doc_type_active = get_attr_or_call(doc, "GetType")
            if doc_type_active != SwDocumentTypes.swDocASSEMBLY:
                return self._result(
                    False,
                    "Active document is not an assembly.",
                    SwErrors.swInvalidDocumentType,
                )

            int_mgr = getattr(doc, "InterferenceDetectionManager", None)
            if int_mgr is None:
                return self._result(
                    False,
                    "InterferenceDetectionManager not available on active assembly.",
                    SwErrors.swInvalidCall,
                )

            int_mgr.TreatCoincidenceAsInterference = bool(treat_coincidence_as_interference)
            interferences = getattr(int_mgr, "GetInterferences", None)

            interference_list = []
            if interferences:
                for i, itf in enumerate(interferences):
                    vol_m3 = getattr(itf, "Volume", 0.0)
                    vol_mm3 = vol_m3 * 1e9
                    comps = getattr(itf, "Components", None)
                    comp_names = [c.Name2 for c in comps] if comps else []
                    coords = getattr(itf, "Coordinates", None)

                    interference_list.append({
                        "index": i,
                        "volume_mm3": round(vol_mm3, 3),
                        "components": comp_names,
                        "center_m": list(coords) if coords else [],
                    })

            # Exit PropertyManager page to restore normal assembly selection
            try:
                int_mgr.Done()
            except Exception as done_err:
                logger.debug(f"int_mgr.Done() notice: {done_err}")

            has_interference = len(interference_list) > 0
            message = (
                f"Detected {len(interference_list)} interference(s)"
                if has_interference
                else "No interference detected across components."
            )

            return self._result(
                True,
                message,
                SwErrors.swSuccess,
                {
                    "has_interference": has_interference,
                    "interference_count": len(interference_list),
                    "interferences": interference_list,
                },
            )
        except Exception as e:
            logger.error(f"check_interference error: {e}")
            return self._result(False, f"Check interference error: {e}", SwErrors.swGeneralError)

    def list_components(self) -> Dict:
        """
        List all components in the active assembly with paths, suppression, and fixed states.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            doc_type_active = get_attr_or_call(doc, "GetType")
            if doc_type_active != SwDocumentTypes.swDocASSEMBLY:
                return self._result(
                    False,
                    "Active document is not an assembly.",
                    SwErrors.swInvalidDocumentType,
                )

            comps = doc.GetComponents(False)
            component_list = []
            if comps:
                for c in comps:
                    name = getattr(c, "Name2", "Unknown")
                    path = getattr(c, "GetPathName", None)
                    path_str = path() if callable(path) else (path or "")
                    is_suppressed = getattr(c, "IsSuppressed", None)
                    suppressed_val = is_suppressed() if callable(is_suppressed) else bool(is_suppressed)
                    is_fixed = getattr(c, "IsFixed", None)
                    fixed_val = is_fixed() if callable(is_fixed) else bool(is_fixed)

                    component_list.append({
                        "name": name,
                        "path": path_str,
                        "suppressed": suppressed_val,
                        "fixed": fixed_val,
                    })

            return self._result(
                True,
                f"Found {len(component_list)} component(s) in assembly",
                SwErrors.swSuccess,
                {
                    "component_count": len(component_list),
                    "components": component_list,
                },
            )
        except Exception as e:
            logger.error(f"list_components error: {e}")
            return self._result(False, f"List components error: {e}", SwErrors.swGeneralError)
