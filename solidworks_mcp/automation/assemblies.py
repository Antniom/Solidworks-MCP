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

            box_center = [0.0, 0.0, 0.0]
            if HAS_PYWIN32:
                errors = byref_int(0)
                warnings = byref_int(0)
                # OpenDoc6 with Option 1 = swOpenDocOptions_Silent
                comp_doc = self._sw_app.OpenDoc6(component_path, int(doc_type), 1, "", errors, warnings)
                # AddComponent4 places the component's bounding-box centre (not its origin) at the
                # given point, so read that centre in the component's own coordinates.
                try:
                    if comp_doc is not None:
                        if doc_type == SwDocumentTypes.swDocPART:
                            bx = comp_doc.GetPartBox(True)
                        else:
                            bx = comp_doc.GetBox(0)
                        if bx and len(bx) >= 6:
                            box_center = [(bx[0] + bx[3]) / 2.0, (bx[1] + bx[4]) / 2.0, (bx[2] + bx[5]) / 2.0]
                except Exception as bx_ex:
                    logger.debug(f"add_component: bbox read failed: {bx_ex}")
                # Ensure assembly remains active
                title = get_attr_or_call(doc, "GetTitle")
                self._sw_app.ActivateDoc3(title, False, 2, errors)

            # Capture pre-state for post-condition verification (F3)
            pre_state = capture_pre_mutation_state(doc)

            # Insert component so that its ORIGIN lands on (x, y, z)
            try:
                comp = doc.AddComponent4(
                    component_path, "",
                    x_m + box_center[0], y_m + box_center[1], z_m + box_center[2],
                )
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

            # Verify the component origin landed on the requested point.
            origin_mm = None
            try:
                got = get_attr_or_call(get_attr_or_call(comp, "Transform2"), "ArrayData")
                origin_mm = [round(float(got[i]) * 1000.0, 3) for i in (9, 10, 11)]
            except Exception:
                origin_mm = None
            target_mm = [round(x_m * 1000.0, 3), round(y_m * 1000.0, 3), round(z_m * 1000.0, 3)]
            if origin_mm is None or any(abs(a - b) > 0.01 for a, b in zip(origin_mm, target_mm)):
                # Report but keep the component (it was inserted); include where it actually is.
                return self._result(
                    False,
                    f"Component '{comp_name}' was inserted, but its origin is at {origin_mm} mm, not at the "
                    f"requested {target_mm} mm. It is still in the assembly.",
                    SwErrors.swGeneralError,
                    {"name": comp_name, "origin_mm": origin_mm, "requested_mm": target_mm, "inserted": True},
                )

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
        min_limit: Optional[float] = None,
        max_limit: Optional[float] = None,
        unit: str = "mm",
        use_selection: bool = False,
    ) -> Dict:
        """
        Add a mate between two entities in the active assembly.
        With use_selection=True the two currently selected entities are mated
        (select them first with select_by_geometry / select_entity).

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

            # "@selected" works even with clients that cached the older schema (no use_selection).
            if str(entity1 or "").strip().lower() in ("@selected", "@selection") or \
               str(entity2 or "").strip().lower() in ("@selected", "@selection"):
                use_selection = True
            if use_selection:
                # Re-select the two current picks with mark 1 (AddMate3 reads marked entities).
                sel_mgr = doc.SelectionManager
                count = int(sel_mgr.GetSelectedObjectCount2(-1) or 0)
                if count != 2:
                    return self._result(
                        False,
                        f"use_selection=true needs exactly 2 selected entities, found {count}.",
                        SwErrors.swSelectionError,
                    )
                picked = [sel_mgr.GetSelectedObject6(i, -1) for i in (1, 2)]
                doc.ClearSelection2(True)
                for i, ent in enumerate(picked):
                    ok_sel = False
                    try:
                        ok_sel = bool(ent.Select2(i > 0, 1))
                    except Exception:
                        ok_sel = False
                    if not ok_sel:
                        # Entities that are not IEntity (e.g. features) use Select4 with a select data object.
                        try:
                            sd = sel_mgr.CreateSelectData()
                            sd.Mark = 1
                            ok_sel = bool(ent.Select4(i > 0, sd))
                        except Exception:
                            ok_sel = False
                    if not ok_sel:
                        return self._result(
                            False, f"Could not re-select selected entity #{i + 1} for the mate.", SwErrors.swSelectionError
                        )
            else:
                if not entity1 or not entity2:
                    return self._result(
                        False,
                        "Pass entity1 and entity2, or pre-select two entities and set use_selection=true.",
                        SwErrors.swInvalidParameter,
                    )
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

            ok1 = True if use_selection else select_target(entity1, comp1, False)
            if not ok1:
                return self._result(
                    False,
                    f"Failed to select first mate entity '{entity1}' (component: {comp1})",
                    SwErrors.swSelectionError,
                )

            ok2 = True if use_selection else select_target(entity2, comp2, True)
            if not ok2:
                return self._result(
                    False,
                    f"Failed to select second mate entity '{entity2}' (component: {comp2})",
                    SwErrors.swSelectionError,
                )

            # Convert dimensional parameters
            dist_m = abs(self._units.to_meters(distance, unit))
            angle_rad = angle * 3.141592653589793 / 180.0
            dist_upper_m = abs(self._units.to_meters(max_limit, unit)) if (max_limit is not None and sw_mate_type == 5) else dist_m
            dist_lower_m = abs(self._units.to_meters(min_limit, unit)) if (min_limit is not None and sw_mate_type == 5) else dist_m
            angle_upper_rad = (float(max_limit) * 3.141592653589793 / 180.0) if (max_limit is not None and sw_mate_type == 6) else angle_rad
            angle_lower_rad = (float(min_limit) * 3.141592653589793 / 180.0) if (min_limit is not None and sw_mate_type == 6) else angle_rad

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
                dist_upper_m,
                dist_lower_m,
                angle_rad,
                angle_upper_rad,
                angle_lower_rad,
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

    # ========================================================================
    # Assembly Kinematics, Rigidity, Motion & Positional Representations
    # (lminventor parity suite)
    # ========================================================================

    def delete_assembly_constraint(self, constraint_name: str) -> Dict[str, Any]:
        """
        Delete a specific mate / assembly constraint by name.
        Matches lminventor's delete_assembly_constraint tool.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            doc_type_active = get_attr_or_call(doc, "GetType")
            if doc_type_active != SwDocumentTypes.swDocASSEMBLY:
                return self._result(False, "Active document is not an assembly.", SwErrors.swInvalidDocumentType)

            null_callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None) if HAS_PYWIN32 else None
            doc.ClearSelection2(True)
            selected = doc.Extension.SelectByID2(constraint_name, "MATE", 0.0, 0.0, 0.0, False, 0, null_callout, 0)
            if not selected:
                selected = doc.Extension.SelectByID2(constraint_name, "", 0.0, 0.0, 0.0, False, 0, null_callout, 0)
            if not selected:
                return self._result(
                    False,
                    f"Mate/constraint '{constraint_name}' not found in assembly.",
                    SwErrors.swSelectionError,
                )

            deleted = bool(doc.Extension.DeleteSelection2(1))
            get_attr_or_call(doc, "EditRebuild3")
            return self._result(
                deleted,
                f"Deleted assembly constraint '{constraint_name}'" if deleted else f"Failed to delete '{constraint_name}'",
                SwErrors.swSuccess if deleted else SwErrors.swGeneralError,
                {"constraint_name": constraint_name, "deleted": deleted},
            )
        except Exception as e:
            logger.error(f"delete_assembly_constraint error: {e}")
            return self._result(False, f"delete_assembly_constraint error: {e}", SwErrors.swGeneralError)

    def manage_subassembly_kinematics(
        self,
        action: str = "set_flexible",
        occurrence_name: str = "",
        exclude_keywords: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Manage component/subassembly kinematic states in an assembly:
        - 'set_grounded' / 'fix': Fix (ground) the specified component occurrence
        - 'set_floating' / 'unfix': Float (unground) the specified component occurrence
        - 'set_flexible': Set a subassembly occurrence to Flexible solving
        - 'set_rigid': Set a subassembly occurrence to Rigid solving
        - 'enforce_rigidity': Fix all internal static components while leaving components matching exclude_keywords floating
        Matches lminventor's manage_subassembly_kinematics tool.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            doc_type_active = get_attr_or_call(doc, "GetType")
            if doc_type_active != SwDocumentTypes.swDocASSEMBLY:
                return self._result(False, "Active document is not an assembly.", SwErrors.swInvalidDocumentType)

            act = (action or "").strip().lower()
            comps = doc.GetComponents(False) or []

            def _find_comp(cname: str):
                for c in comps:
                    n2 = str(getattr(c, "Name2", ""))
                    if n2.lower() == cname.lower() or cname.lower() in n2.lower():
                        return c
                return None

            if act == "enforce_rigidity":
                excl = [k.lower() for k in (exclude_keywords or ["porta", "door", "gaveta", "drawer", "tambor", "drum", "hinge", "dobradica", "carril", "rail"])]
                fixed_names = []
                skipped_names = []
                for c in comps:
                    n2 = str(getattr(c, "Name2", ""))
                    if any(k in n2.lower() for k in excl):
                        skipped_names.append(n2)
                        continue
                    try:
                        doc.ClearSelection2(True)
                        c.Select2(False, 0)
                        doc.FixComponent()
                        fixed_names.append(n2)
                    except Exception:
                        pass
                doc.ClearSelection2(True)
                get_attr_or_call(doc, "EditRebuild3")
                return self._result(
                    True,
                    f"Enforced rigidity: fixed {len(fixed_names)} component(s), kept {len(skipped_names)} kinematic component(s) free.",
                    SwErrors.swSuccess,
                    {"fixed": fixed_names, "excluded_free": skipped_names},
                )

            target = _find_comp(occurrence_name)
            if target is None:
                return self._result(
                    False,
                    f"Component '{occurrence_name}' not found in assembly.",
                    SwErrors.swSelectionError,
                )

            comp_name = str(getattr(target, "Name2", occurrence_name))
            doc.ClearSelection2(True)
            target.Select2(False, 0)

            if act in ("set_grounded", "fix", "ground"):
                doc.FixComponent()
                get_attr_or_call(doc, "EditRebuild3")
                return self._result(
                    True,
                    f"Component '{comp_name}' is now Fixed (Grounded).",
                    SwErrors.swSuccess,
                    {"component": comp_name, "action": act, "fixed": True},
                )
            elif act in ("set_floating", "unfix", "float"):
                doc.UnfixComponent()
                get_attr_or_call(doc, "EditRebuild3")
                return self._result(
                    True,
                    f"Component '{comp_name}' is now Floating (Unfixed).",
                    SwErrors.swSuccess,
                    {"component": comp_name, "action": act, "fixed": False},
                )
            elif act in ("set_flexible", "flexible", "set_rigid", "rigid"):
                want_flexible = act in ("set_flexible", "flexible")
                # Flexible/rigid solving only exists for subassemblies, not part components.
                try:
                    comp_path = str(get_attr_or_call(target, "GetPathName") or "")
                except Exception:
                    comp_path = ""
                if comp_path.lower().endswith(".sldprt"):
                    return self._result(
                        False,
                        f"'{comp_name}' is a part, not a subassembly; flexible/rigid solving only applies to subassemblies.",
                        SwErrors.swInvalidParameter,
                        {"component": comp_name, "path": comp_path},
                    )
                # swComponentSolvingOption_e: 0 = swComponentRigidSolving, 1 = swComponentFlexibleSolving
                solving_code = 1 if want_flexible else 0
                applied = False
                try:
                    # CompConfigProperties5(Suppression, Solving, Visibility, UseNamedRefConfig, RefConfigName, ExcludeFromBOM, IsEnvelope)
                    ref_cfg = str(get_attr_or_call(target, "ReferencedConfiguration") or "Default")
                    applied = bool(doc.CompConfigProperties5(2, solving_code, True, True, ref_cfg, False, False))
                except Exception:
                    try:
                        applied = bool(doc.CompConfigProperties4(2, solving_code, True, True, ""))
                    except Exception:
                        pass
                get_attr_or_call(doc, "EditRebuild3")
                state_label = "Flexible" if want_flexible else "Rigid"
                if not applied:
                    return self._result(
                        False,
                        f"Could not set '{comp_name}' to {state_label} solving (CompConfigProperties returned False).",
                        SwErrors.swGeneralError,
                        {"component": comp_name, "solving": state_label.lower(), "applied": False},
                    )
                return self._result(
                    True,
                    f"Subassembly '{comp_name}' set to {state_label} solving (applied={applied}).",
                    SwErrors.swSuccess,
                    {"component": comp_name, "solving": state_label.lower(), "applied": applied},
                )
            else:
                return self._result(
                    False,
                    f"Unknown action '{action}'. Use 'set_grounded', 'set_floating', 'set_flexible', 'set_rigid', or 'enforce_rigidity'.",
                    SwErrors.swInvalidParameter,
                )
        except Exception as e:
            logger.error(f"manage_subassembly_kinematics error: {e}")
            return self._result(False, f"manage_subassembly_kinematics error: {e}", SwErrors.swGeneralError)

    def inspect_assembly_kinematics(self) -> Dict[str, Any]:
        """
        Audit assembly components (Fixed vs Floating, Rigid vs Flexible, suppressed)
        and all mates under MateGroup (type, error status, suppression).
        Matches lminventor's inspect_assembly_kinematics tool.
        """
        try:
            from ..core.com_compat import byref_bool

            doc, err = self.get_active_doc()
            if err:
                return err

            doc_type_active = get_attr_or_call(doc, "GetType")
            if doc_type_active != SwDocumentTypes.swDocASSEMBLY:
                return self._result(False, "Active document is not an assembly.", SwErrors.swInvalidDocumentType)

            comps = doc.GetComponents(False) or []
            comp_records = []
            fixed_count = 0
            floating_count = 0
            flexible_count = 0

            for c in comps:
                name = str(getattr(c, "Name2", "Unknown"))
                is_fixed = bool(get_attr_or_call(c, "IsFixed"))
                is_supp = bool(get_attr_or_call(c, "IsSuppressed"))
                solving_val = get_attr_or_call(c, "Solving")
                is_flex = (solving_val == 1)
                if is_fixed:
                    fixed_count += 1
                else:
                    floating_count += 1
                if is_flex:
                    flexible_count += 1
                comp_records.append({
                    "name": name,
                    "fixed": is_fixed,
                    "suppressed": is_supp,
                    "solving": "flexible" if is_flex else "rigid",
                })

            mates_records = []
            healthy_mates = 0
            sick_mates = 0
            try:
                feat = doc.FirstFeature
                while feat is not None:
                    ftype = str(get_attr_or_call(feat, "GetTypeName2") or "")
                    if ftype == "MateGroup":
                        sub = get_attr_or_call(feat, "GetFirstSubFeature")
                        while sub is not None:
                            mname = str(getattr(sub, "Name", "Mate"))
                            mtype = str(get_attr_or_call(sub, "GetTypeName2") or "Mate")
                            is_warn = byref_bool(False)
                            err_code = 0
                            try:
                                err_code = int(sub.GetErrorCode2(is_warn) or 0)
                            except Exception:
                                pass
                            healthy = (err_code == 0)
                            if healthy:
                                healthy_mates += 1
                            else:
                                sick_mates += 1
                            mates_records.append({
                                "name": mname,
                                "type": mtype,
                                "error_code": err_code,
                                "is_warning": bool(is_warn.value),
                                "healthy": healthy,
                            })
                            sub = get_attr_or_call(sub, "GetNextSubFeature")
                        break
                    feat = feat.GetNextFeature
            except Exception as m_ex:
                logger.debug(f"MateGroup traversal warning: {m_ex}")

            msg = (
                f"Kinematics audit: {len(comp_records)} components ({fixed_count} fixed, {floating_count} floating, "
                f"{flexible_count} flexible), {len(mates_records)} mates ({healthy_mates} healthy, {sick_mates} with errors)."
            )
            return self._result(
                True,
                msg,
                SwErrors.swSuccess,
                {
                    "component_count": len(comp_records),
                    "fixed_count": fixed_count,
                    "floating_count": floating_count,
                    "flexible_count": flexible_count,
                    "components": comp_records,
                    "mate_count": len(mates_records),
                    "healthy_mates": healthy_mates,
                    "sick_mates": sick_mates,
                    "mates": mates_records,
                },
            )
        except Exception as e:
            logger.error(f"inspect_assembly_kinematics error: {e}")
            return self._result(False, f"inspect_assembly_kinematics error: {e}", SwErrors.swGeneralError)

    def drive_kinematic_motion(
        self,
        constraint_name: str,
        start_value: float = 0.0,
        end_value: float = 90.0,
        steps: int = 10,
        unit: str = "mm",
        check_interference: bool = False,
    ) -> Dict[str, Any]:
        """
        Step-interpolate a driving mate dimension (e.g. 'D1@Distance1' or 'Distance1')
        from start_value to end_value across steps with live viewport updates and optional
        collision detection at each step.
        Matches lminventor's drive_kinematic_motion tool.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            n_steps = max(1, int(steps))
            dim_name = constraint_name if "@" in constraint_name else f"D1@{constraint_name}"
            step_results = []
            collisions_detected = 0

            for i in range(n_steps + 1):
                t = i / float(n_steps)
                val = float(start_value) + t * (float(end_value) - float(start_value))
                set_res = self.set_dimension(name=dim_name, value=val, units=unit)
                if not set_res.get("success") and "@" not in constraint_name:
                    set_res = self.set_dimension(name=constraint_name, value=val, units=unit)
                try:
                    doc.EditRebuild3()
                    doc.GraphicsRedraw2()
                except Exception:
                    pass

                step_entry: Dict[str, Any] = {
                    "step": i,
                    "value": round(val, 3),
                    "updated": bool(set_res.get("success")),
                }
                if check_interference:
                    itf_res = self.check_interference(treat_coincidence_as_interference=False)
                    itf_cnt = itf_res.get("data", {}).get("interference_count", 0)
                    step_entry["interferences"] = itf_cnt
                    if itf_cnt > 0:
                        collisions_detected += 1
                step_results.append(step_entry)

            return self._result(
                True,
                f"Drove '{constraint_name}' from {start_value} to {end_value} ({unit}) across {n_steps} steps "
                f"(collisions={collisions_detected})",
                SwErrors.swSuccess,
                {
                    "constraint_name": constraint_name,
                    "dimension_name": dim_name,
                    "start_value": start_value,
                    "end_value": end_value,
                    "steps": n_steps,
                    "unit": unit,
                    "collisions_detected": collisions_detected,
                    "trajectory": step_results,
                },
            )
        except Exception as e:
            logger.error(f"drive_kinematic_motion error: {e}")
            return self._result(False, f"drive_kinematic_motion error: {e}", SwErrors.swGeneralError)

    def manage_positional_representations(
        self,
        action: str = "list",
        name: Optional[str] = None,
        constraint_name: Optional[str] = None,
        value: Optional[str] = None,
        suppress_constraint: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Manage named mechanism states (Positional Representations) in SolidWorks via Assembly Configurations
        and configuration-specific mate dimension/suppression overrides.
        action: 'list' | 'create' | 'activate' | 'set_override'
        Matches lminventor's manage_positional_representations tool.
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            act = (action or "list").strip().lower()
            cfg_names = list(get_attr_or_call(doc, "GetConfigurationNames") or [])
            active_cfg_obj = get_attr_or_call(doc, "GetActiveConfiguration")
            active_cfg_name = str(getattr(active_cfg_obj, "Name", "Default")) if active_cfg_obj else "Default"

            if act == "list":
                return self._result(
                    True,
                    f"Positional representations (configurations): {cfg_names} (active='{active_cfg_name}')",
                    SwErrors.swSuccess,
                    {"representations": cfg_names, "active": active_cfg_name},
                )

            if not name:
                return self._result(False, "Parameter 'name' is required for create/activate/set_override.", SwErrors.swInvalidParameter)

            if act == "create":
                if name not in cfg_names:
                    cfg_mgr = getattr(doc, "ConfigurationManager", None)
                    if cfg_mgr is not None:
                        cfg_mgr.AddConfiguration2(name, f"Positional Representation {name}", "", 0, "", "", True)
                    else:
                        doc.AddConfiguration3(name, f"Positional Representation {name}", "", 0)
                doc.ShowConfiguration2(name)
                return self._result(
                    True,
                    f"Created and activated positional representation '{name}'",
                    SwErrors.swSuccess,
                    {"name": name, "active": name},
                )

            elif act == "activate":
                ok = bool(doc.ShowConfiguration2(name))
                get_attr_or_call(doc, "EditRebuild3")
                return self._result(
                    ok,
                    f"Activated positional representation '{name}'" if ok else f"Configuration '{name}' not found",
                    SwErrors.swSuccess if ok else SwErrors.swInvalidParameter,
                    {"name": name, "activated": ok},
                )

            elif act == "set_override":
                if name not in cfg_names:
                    self.manage_positional_representations(action="create", name=name)
                else:
                    doc.ShowConfiguration2(name)

                if not constraint_name:
                    return self._result(False, "constraint_name is required for set_override.", SwErrors.swInvalidParameter)

                override_result = {}
                if suppress_constraint is not None:
                    act_str = "suppress" if suppress_constraint else "unsuppress"
                    override_result["suppression"] = self.manage_feature(
                        action=act_str,
                        feature_name=constraint_name,
                        config_scope=1,  # swThisConfiguration = 1
                    )

                if value is not None:
                    raw_val = str(value).strip().lower()
                    unit_inferred = "deg" if "deg" in raw_val else "mm"
                    num_str = "".join(ch for ch in raw_val if (ch.isdigit() or ch in ".-+"))
                    num_val = float(num_str) if num_str else 0.0
                    dim_target = constraint_name if "@" in constraint_name else f"D1@{constraint_name}"
                    override_result["dimension"] = self.set_dimension(
                        name=dim_target,
                        value=num_val,
                        units=unit_inferred,
                        config_scope="this_configuration",
                    )

                get_attr_or_call(doc, "EditRebuild3")
                return self._result(
                    True,
                    f"Set override on '{constraint_name}' in positional representation '{name}' (value={value}, suppress={suppress_constraint})",
                    SwErrors.swSuccess,
                    {
                        "representation": name,
                        "constraint_name": constraint_name,
                        "value": value,
                        "suppress_constraint": suppress_constraint,
                        "details": override_result,
                    },
                )

            else:
                return self._result(
                    False,
                    f"Unknown action '{action}'. Use 'list', 'create', 'activate', or 'set_override'.",
                    SwErrors.swInvalidParameter,
                )
        except Exception as e:
            logger.error(f"manage_positional_representations error: {e}")
            return self._result(False, f"manage_positional_representations error: {e}", SwErrors.swGeneralError)
