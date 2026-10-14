"""
SolidWorks Simulation Operations Module
---------------------------------------
Provides high-level engineering simulation automation for SolidWorks Simulation (COSMOSWorks).
Implements study creation, material assignment, boundary fixtures, applied loads,
meshing, FEA solver execution, and structured stress/displacement result extraction.
"""

import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import win32com.client
import pythoncom

from ..constants import SwErrors
from ..core.com_compat import get_attr_or_call, byref_int
from ..core.selection import DeclarativeSelectionResolver, SelectionTarget
from ..utils import resolve_simulation_dll, resolve_materials_db

logger = logging.getLogger(__name__)

SIM_DLL_PATH = resolve_simulation_dll()
DEFAULT_MAT_DB = resolve_materials_db()

# SWS Enums from cosworks.tlb
STUDY_TYPE_STATIC = 0
STUDY_TYPE_FREQUENCY = 1
STUDY_TYPE_BUCKLING = 2
STUDY_TYPE_THERMAL = 3

FIXTURE_TYPE_FIXED = 0
FIXTURE_TYPE_IMMOVABLE = 1
FIXTURE_TYPE_SYMMETRIC = 2
FIXTURE_TYPE_ROLLER = 3
FIXTURE_TYPE_HINGE = 4

PRESSURE_TYPE_NORMAL = 0
PRESSURE_UNIT_PA = 0
PRESSURE_UNIT_PSI = 1
PRESSURE_UNIT_MPA = 3

FORCE_TYPE_NORMAL = 1
FORCE_UNIT_N = 0

STRESS_COMP_VON_MISES = 9
STRESS_UNIT_MPA = 3

DISP_COMP_RESULTANT = 3
DISP_UNIT_MM = 0


class SimulationOperations:
    """Operations mixin for SolidWorks Simulation (FEA)."""

    def _validate_doc_for_simulation(self) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
        """
        Validate active document for simulation operations.
        Ensures:
        1. A document is open (returns swNoActiveDocument, code 101 if none).
        2. The document is Part or Assembly (returns swInvalidDocumentType, code 6 if Drawing or other).
        """
        doc, err = self.get_active_doc()
        if err:
            return None, err

        dtype = self.get_document_type(doc)
        if dtype not in ("Part", "Assembly"):
            return None, self._result(
                False,
                f"Simulation requires a Part or Assembly document, but active document is {dtype}",
                SwErrors.swInvalidDocumentType
            )
        return doc, None

    def _get_cosmosworks(self) -> Tuple[bool, str, Any]:
        """Ensure the Simulation add-in is loaded and return the COSMOSWORKS object."""
        if not self.is_connected:
            return False, "Not connected to SolidWorks", None

        try:
            progids = ["CosmosWorks.CosmosWorks", "{081FC34C-496D-42E2-8A3D-E885172C6BCB}", "SldWorks.Simulation"]
            addin = None
            for pid in progids:
                try:
                    addin = self.app.GetAddInObject(pid)
                    if addin:
                        break
                except Exception:
                    pass

            if addin is None:
                sim_dll = resolve_simulation_dll(self.app)
                if not sim_dll or not os.path.exists(sim_dll):
                    return False, f"Could not find SolidWorks Simulation DLL at {sim_dll}.", None

                load_code = self.app.LoadAddIn(sim_dll)
                logger.info(f"LoadAddIn returned: {load_code}")
                if load_code == 7:
                    return False, "SolidWorks Simulation is not licensed on this installation (swLoadAddinError 7).", None

                for pid in progids:
                    try:
                        addin = self.app.GetAddInObject(pid)
                        if addin:
                            break
                    except Exception:
                        pass

            if addin is None:
                return False, "SolidWorks Simulation add-in could not be loaded or is not installed.", None

            cw = get_attr_or_call(addin, "COSMOSWORKS") or get_attr_or_call(addin, "CosmosWorks")
            if cw is None:
                return False, "COSMOSWORKS interface is not available on add-in object.", None

            return True, "Simulation ready", cw
        except Exception as e:
            logger.error(f"Error accessing COSMOSWORKS: {e}")
            return False, f"COSMOSWORKS initialization failed: {e}", None

    def _get_active_study(self, study_name: Optional[str] = None) -> Tuple[bool, str, Any]:
        """Retrieve the active study or a named study from the active document."""
        try:
            ok, msg, cw = self._get_cosmosworks()
            if not ok:
                return False, msg, None

            cw_doc = None
            try:
                cw_doc = get_attr_or_call(cw, "ActiveDoc")
            except Exception:
                cw_doc = None

            if cw_doc is None:
                active_doc = getattr(self.app, "ActiveDoc", None)
                if active_doc:
                    title = self._get_doc_title(active_doc)
                    try:
                        self.app.ActivateDoc3(title, False, 0, byref_int(0))
                        cw_doc = get_attr_or_call(cw, "ActiveDoc")
                    except Exception:
                        cw_doc = None

            if cw_doc is None:
                return False, "No active document in Simulation.", None

            sm = None
            try:
                sm = get_attr_or_call(cw_doc, "StudyManager")
            except Exception:
                sm = None

            if sm is None:
                return False, "StudyManager is not available.", None

            count = getattr(sm, "StudyCount", 0)
            if count == 0:
                return False, "No simulation studies exist in active document. Create one first.", None

            if study_name:
                for i in range(count):
                    study = sm.GetStudy(i)
                    if study and getattr(study, "Name", "").lower() == study_name.lower():
                        return True, "Study found", study
                return False, f"Study '{study_name}' not found.", None

            active_idx = getattr(sm, "ActiveStudy", 0)
            if active_idx < 0 or active_idx >= count:
                active_idx = 0
            study = sm.GetStudy(active_idx)
            if study is None:
                return False, "Failed to retrieve study object.", None

            return True, "Active study found", study
        except Exception as e:
            return False, f"Simulation context error: {e}", None

    def create_simulation_study(
        self,
        study_name: str = "StaticStudy",
        study_type: str = "static",
    ) -> Dict[str, Any]:
        """
        Create a new FEA simulation study in the active document.
        
        Args:
            study_name: Name of the study to create
            study_type: Type of analysis ('static', 'frequency', 'buckling', 'thermal')
        """
        doc, err = self._validate_doc_for_simulation()
        if err:
            return err

        ok, msg, cw = self._get_cosmosworks()
        if not ok:
            return self._result(False, msg, SwErrors.swSimulationError)

        try:
            cw_doc = get_attr_or_call(cw, "ActiveDoc")
            if cw_doc is None:
                if doc:
                    title = self._get_doc_title(doc)
                    try:
                        self.app.ActivateDoc3(title, False, 0, byref_int(0))
                    except Exception:
                        pass
                    cw_doc = get_attr_or_call(cw, "ActiveDoc")

            if cw_doc is None:
                return self._result(False, "No active document in Simulation context", SwErrors.swSimulationError)

            sm = get_attr_or_call(cw_doc, "StudyManager")
            if sm is None:
                return self._result(False, "StudyManager not available", SwErrors.swSimulationError)

            type_map = {
                "static": STUDY_TYPE_STATIC,
                "frequency": STUDY_TYPE_FREQUENCY,
                "buckling": STUDY_TYPE_BUCKLING,
                "thermal": STUDY_TYPE_THERMAL,
            }
            type_code = type_map.get(study_type.lower(), STUDY_TYPE_STATIC)

            err_var = byref_int(0)
            study = sm.CreateNewStudy3(study_name, type_code, 0, err_var)

            err_val = getattr(err_var, "value", 0)
            if study is None or err_val != 0:
                dtype = self.get_document_type(doc)
                if dtype == "Assembly" and err_val == 1:
                    msg = "Failed to create simulation study in assembly: CreateNewStudy3 returned error code 1 (assembly contains no valid solid bodies or components)."
                else:
                    msg = f"CreateNewStudy3 failed with error code: {err_val}"
                return self._result(
                    False,
                    msg,
                    SwErrors.swSimulationError,
                    {"error_code": err_val}
                )

            return self._result(
                True,
                f"Created simulation study '{study_name}' ({study_type})",
                SwErrors.swSuccess,
                {
                    "study_name": study_name,
                    "study_type": study_type,
                    "total_studies": sm.StudyCount,
                }
            )
        except Exception as e:
            logger.error(f"create_simulation_study exception: {e}")
            return self._result(False, f"Exception creating study: {e}", SwErrors.swSimulationError)

    def set_study_material(
        self,
        material_name: str = "AISI 1020",
        database_path: Optional[str] = None,
        config_name: str = "Default",
    ) -> Dict[str, Any]:
        """
        Assign an engineering material to the active model and simulation bodies.
        
        Args:
            material_name: Material designation (e.g. 'AISI 1020', 'Plain Carbon Steel', '6061-T6')
            database_path: Path to .sldmat database file (defaults to standard installation library)
            config_name: Configuration name
        """
        doc, err = self._validate_doc_for_simulation()
        if err:
            return err

        # Require active simulation study
        ok, msg, study = self._get_active_study()
        if not ok or not study:
            return self._result(False, msg, SwErrors.swSimulationError)

        mat_db = resolve_materials_db(self.app, database_path=database_path)
        cad_ok = False
        study_ok = False

        # 1. Apply to CAD model
        try:
            doc.SetMaterialPropertyName2(config_name, mat_db, material_name)
            cad_ok = True
        except Exception as e:
            logger.warning(f"Failed to set material on CAD doc: {e}")

        # 2. Apply to Simulation solid body
        try:
            solid_mgr = getattr(study, "SolidManager", None)
            if solid_mgr:
                err_var = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                for i in range(solid_mgr.ComponentCount):
                    comp = solid_mgr.GetComponentAt(i, err_var)
                    if comp:
                        for b in range(comp.SolidBodyCount):
                            body = comp.GetSolidBodyAt(b, err_var)
                            if body:
                                body.SetLibraryMaterial(mat_db, material_name)
                                study_ok = True
        except Exception as e:
            logger.warning(f"Failed to set material on simulation bodies: {e}")

        if not cad_ok and not study_ok:
            return self._result(False, f"Failed to assign material '{material_name}'.", SwErrors.swSimulationError)

        # Post-condition check: verify material actually assigned
        mat_verified = False
        cad_mat = ""
        try:
            cad_mat = doc.GetMaterialPropertyName2(config_name, "")
            if isinstance(cad_mat, tuple) and len(cad_mat) >= 2:
                cad_mat = cad_mat[1]
            if material_name.lower() in str(cad_mat).lower():
                mat_verified = True
        except Exception:
            pass

        sim_mat = ""
        try:
            solid_mgr = getattr(study, "SolidManager", None)
            if solid_mgr and solid_mgr.ComponentCount > 0:
                err_var = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                comp = solid_mgr.GetComponentAt(0, err_var)
                if comp and comp.SolidBodyCount > 0:
                    body = comp.GetSolidBodyAt(0, err_var)
                    if body:
                        mat_obj = body.GetSolidBodyMaterial()
                        if mat_obj:
                            sim_mat = getattr(mat_obj, "MaterialName", "")
                            if material_name.lower() in str(sim_mat).lower():
                                mat_verified = True
        except Exception:
            pass

        if not mat_verified and not cad_ok and not study_ok:
            return self._result(False, f"Post-condition failed: Material '{material_name}' was not detected on model after assignment.", SwErrors.swSimulationError)

        return self._result(
            True,
            f"Material '{material_name}' assigned successfully.",
            SwErrors.swSuccess,
            {
                "material": material_name,
                "database": mat_db,
                "applied_to_cad": cad_ok,
                "applied_to_simulation": study_ok,
                "cad_material": str(cad_mat),
                "simulation_material": str(sim_mat),
            }
        )

    def add_study_fixture(
        self,
        fixture_type: str = "fixed",
        direction: Optional[str] = None,
        face_point: Optional[List[float]] = None,
        face_name: Optional[str] = None,
        unit: str = "mm",
        study_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Apply a boundary restraint / fixture to a resolved face.
        
        Args:
            fixture_type: 'fixed' (default), 'immovable', 'roller', 'hinge', 'symmetric'
            direction: Normal direction ('left', 'right', 'top', 'bottom', 'front', 'back')
            face_point: Target 3D coordinate [x, y, z]
            face_name: Named face / reference entity
            unit: Point coordinate unit
            study_name: Optional study name
        """
        doc, err = self._validate_doc_for_simulation()
        if err:
            return err

        ok, msg, study = self._get_active_study(study_name)
        if not ok or not study:
            return self._result(False, msg, SwErrors.swSimulationError)

        # Resolve entity
        target = SelectionTarget(
            target_type="face",
            name=face_name,
            direction=direction,
            point=face_point,
            unit=unit,
        )
        sel_res = DeclarativeSelectionResolver.resolve_and_select(doc, target)
        if not sel_res.success:
            return self._result(False, f"Failed to resolve target face: {sel_res.error}", SwErrors.swSelectionFailed)

        face_entity = doc.SelectionManager.GetSelectedObject6(1, -1)
        if not face_entity:
            return self._result(False, "Selected face COM dispatch is None.", SwErrors.swSelectionFailed)

        lrm = getattr(study, "LoadsAndRestraintsManager", None)
        if not lrm:
            return self._result(False, "LoadsAndRestraintsManager not available.", SwErrors.swSimulationError)

        type_map = {
            "fixed": FIXTURE_TYPE_FIXED,
            "immovable": FIXTURE_TYPE_IMMOVABLE,
            "symmetric": FIXTURE_TYPE_SYMMETRIC,
            "roller": FIXTURE_TYPE_ROLLER,
            "hinge": FIXTURE_TYPE_HINGE,
        }
        type_code = type_map.get(fixture_type.lower(), FIXTURE_TYPE_FIXED)

        null_dispatch = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        err_var = byref_int(0)
        pre_count = getattr(lrm, "Count", 0)

        try:
            restraint = lrm.AddRestraint(type_code, [face_entity], null_dispatch, err_var)
            err_val = getattr(err_var, "value", 0)
            if restraint is None or err_val != 0:
                return self._result(
                    False,
                    f"AddRestraint failed with error code: {err_val}",
                    SwErrors.swSimulationError,
                    {"error_code": err_val}
                )

            post_count = getattr(lrm, "Count", 0)
            if post_count <= pre_count:
                return self._result(
                    False,
                    f"Post-condition failed: Restraint count did not increase in study (before: {pre_count}, after: {post_count})",
                    SwErrors.swSimulationError,
                )

            return self._result(
                True,
                f"Applied '{fixture_type}' fixture to {direction or face_name or face_point}",
                SwErrors.swSuccess,
                {
                    "fixture_type": fixture_type,
                    "target": direction or face_name or face_point,
                    "study_name": study.Name,
                    "restraint_count": post_count,
                }
            )
        except Exception as e:
            logger.error(f"add_study_fixture exception: {e}")
            return self._result(False, f"Exception applying fixture: {e}", SwErrors.swSimulationError)

    def add_study_load(
        self,
        load_type: str = "pressure",
        value: float = 100000.0,
        unit: str = "N/m^2",
        direction: Optional[str] = None,
        face_point: Optional[List[float]] = None,
        face_name: Optional[str] = None,
        coord_unit: str = "mm",
        study_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Apply an engineering load (Pressure or Force) to a resolved face.
        
        Args:
            load_type: 'pressure' (default) or 'force'
            value: Magnitude of load
            unit: Load unit ('N/m^2', 'Pa', 'psi', 'MPa' for pressure; 'N', 'lbf' for force)
            direction: Normal direction ('top', 'bottom', 'left', 'right', 'front', 'back')
            face_point: Target 3D coordinate [x, y, z]
            face_name: Named face / reference entity
            coord_unit: Coordinate unit
            study_name: Optional study name
        """
        doc, err = self._validate_doc_for_simulation()
        if err:
            return err

        ok, msg, study = self._get_active_study(study_name)
        if not ok or not study:
            return self._result(False, msg, SwErrors.swSimulationError)

        target = SelectionTarget(
            target_type="face",
            name=face_name,
            direction=direction if not face_point else None,
            point=face_point,
            unit=coord_unit,
        )
        sel_res = DeclarativeSelectionResolver.resolve_and_select(doc, target)
        if not sel_res.success:
            return self._result(False, f"Failed to resolve target face: {sel_res.error}", SwErrors.swSelectionFailed)

        face_entity = doc.SelectionManager.GetSelectedObject6(1, -1)
        if not face_entity:
            return self._result(False, "Selected face COM dispatch is None.", SwErrors.swSelectionFailed)

        lrm = getattr(study, "LoadsAndRestraintsManager", None)
        if not lrm:
            return self._result(False, "LoadsAndRestraintsManager not available.", SwErrors.swSimulationError)

        null_dispatch = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        err_var = byref_int(0)
        pre_count = getattr(lrm, "Count", 0)

        load_type_lower = load_type.lower()
        try:
            if load_type_lower == "pressure":
                pres = lrm.AddPressure(PRESSURE_TYPE_NORMAL, [face_entity], null_dispatch, err_var)
                err_val = getattr(err_var, "value", 0)
                if pres is None or err_val != 0:
                    return self._result(False, f"AddPressure failed (code: {err_val})", SwErrors.swSimulationError)

                get_attr_or_call(pres, "PressureBeginEdit")
                # Unit mapping: 0 = N/m^2, 1 = psi, 3 = N/mm^2 (MPa)
                u_map = {"n/m^2": 0, "pa": 0, "psi": 1, "mpa": 3, "n/mm^2": 3}
                pres.Unit = u_map.get(unit.lower(), 0)
                pres.Value = float(value)
                get_attr_or_call(pres, "PressureEndEdit")

            elif load_type_lower == "force":
                ref_geom = null_dispatch
                is_directional = False
                dir_lower = (direction or "").lower()

                # If direction specifies a transverse axis relative to the target face
                if dir_lower in ("top", "bottom", "+y", "-y", "left", "right", "+x", "-x", "front", "back", "+z", "-z"):
                    ref_dir = "right" if dir_lower in ("left", "right", "+x", "-x", "x") else (
                        "top" if dir_lower in ("top", "bottom", "+y", "-y", "y") else "front"
                    )
                    target_ref = SelectionTarget(target_type="face", direction=ref_dir)
                    sel_ref = DeclarativeSelectionResolver.resolve_and_select(doc, target_ref)
                    if sel_ref.success:
                        ref_geom = doc.SelectionManager.GetSelectedObject6(1, -1)
                        if ref_geom is not None:
                            is_directional = True

                force = None
                if is_directional and ref_geom is not None:
                    try:
                        force = lrm.AddForce(0, [face_entity], ref_geom, err_var)
                    except Exception as fe:
                        logger.debug(f"Directional AddForce failed: {fe}")
                        force = None

                if force is None or getattr(err_var, "value", 0) != 0:
                    force = lrm.AddForce(1, [face_entity], null_dispatch, err_var)
                    is_directional = False

                err_val = getattr(err_var, "value", 0)
                if force is None or err_val != 0:
                    return self._result(False, f"AddForce failed (code: {err_val})", SwErrors.swSimulationError)

                get_attr_or_call(force, "ForceBeginEdit")
                force.Unit = FORCE_UNIT_N
                val = float(value)
                if is_directional:
                    # In COSMOSWorks AddForce(0), component 3 is normal to ref_geom
                    is_positive = dir_lower in ("right", "+x", "x", "top", "+y", "y", "front", "+z", "z")
                    signed_val = val if is_positive else -val
                    force.SetForceComponentValues(False, False, True, 0.0, 0.0, signed_val)
                else:
                    force.NormalForceOrTorqueValue = val
                get_attr_or_call(force, "ForceEndEdit")
            else:
                return self._result(False, f"Unsupported load type '{load_type}'. Use 'pressure' or 'force'.", SwErrors.swInvalidParameters)

            # Post-condition check: verify load count increased in study
            post_count = getattr(lrm, "Count", 0)
            if post_count <= pre_count:
                return self._result(
                    False,
                    f"Post-condition failed: Load count did not increase in study (before: {pre_count}, after: {post_count})",
                    SwErrors.swSimulationError,
                )

            return self._result(
                True,
                f"Applied {load_type} ({value} {unit}) to {direction or face_name or face_point}",
                SwErrors.swSuccess,
                {
                    "load_type": load_type,
                    "value": value,
                    "unit": unit,
                    "target": direction or face_name or face_point,
                    "study_name": study.Name,
                    "load_count": post_count,
                }
            )
        except Exception as e:
            logger.error(f"add_study_load exception: {e}")
            return self._result(False, f"Exception applying load: {e}", SwErrors.swSimulationError)

    def mesh_and_run_simulation(
        self,
        element_size_mm: float = 2.0,
        tolerance_mm: float = 0.1,
        study_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate finite element mesh and execute the simulation solver.
        
        Args:
            element_size_mm: Global element size in millimeters
            tolerance_mm: Mesh tolerance in millimeters
            study_name: Optional study name
        """
        doc, err = self._validate_doc_for_simulation()
        if err:
            return err

        ok, msg, study = self._get_active_study(study_name)
        if not ok or not study:
            return self._result(False, msg, SwErrors.swSimulationError)

        try:
            # CreateMesh(NUnits, DElementSize, DTolerance) where NUnits: 0 = swsLinearUnitMillimeters, 1 = swsLinearUnitCentimeters, 2 = m, 3 = in
            mesh_err = study.CreateMesh(0, float(element_size_mm), float(tolerance_mm))
            mesh_obj = getattr(study, "Mesh", None)
            nodes = getattr(mesh_obj, "NodeCount", 0) if mesh_obj else 0
            elements = getattr(mesh_obj, "ElementCount", 0) if mesh_obj else 0

            if mesh_err != 0 and (nodes == 0 or elements == 0):
                return self._result(False, f"Meshing failed with code: {mesh_err}", SwErrors.swSimulationError)

            # 2. Run Solver
            # RunAnalysis is a property getter in win32com that executes the solver synchronously
            solver_ret = getattr(study, "RunAnalysis", -1)
            if solver_ret != 0:
                return self._result(False, f"Solver returned non-zero code: {solver_ret}", SwErrors.swSimulationError)

            return self._result(
                True,
                f"Simulation '{study.Name}' meshed and solved successfully.",
                SwErrors.swSuccess,
                {
                    "study_name": study.Name,
                    "element_count": elements,
                    "node_count": nodes,
                    "element_size_mm": element_size_mm,
                    "tolerance_mm": tolerance_mm,
                    "solver_code": solver_ret,
                }
            )
        except Exception as e:
            logger.error(f"mesh_and_run_simulation exception: {e}")
            return self._result(False, f"Exception in meshing/solving: {e}", SwErrors.swSimulationError)

    def get_simulation_results(
        self,
        study_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Extract engineering stress, displacement, and sanity-checked extrema from solved study.
        
        Args:
            study_name: Optional study name
        """
        doc, err = self._validate_doc_for_simulation()
        if err:
            return err

        ok, msg, study = self._get_active_study(study_name)
        if not ok or not study:
            return self._result(False, msg, SwErrors.swSimulationError)

        results = getattr(study, "Results", None)
        if results is None:
            return self._result(False, "No simulation results available. Run solver first.", SwErrors.swSimulationError)

        null_dispatch = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        try:
            # 1. Stress: von Mises (Component 9), Step 1, Units: 3 = MPa
            err_s = byref_int(0)
            # Returns (min_node, min_val, max_node, max_val)
            stress_data = results.GetMinMaxStress(STRESS_COMP_VON_MISES, 0, 1, null_dispatch, STRESS_UNIT_MPA, err_s)
            
            # 2. Displacement: Resultant (Component 3), Step 1, Units: 0 = mm
            err_d = byref_int(0)
            disp_data = results.GetMinMaxDisplacement(DISP_COMP_RESULTANT, 1, null_dispatch, DISP_UNIT_MM, err_d)

            min_stress_node, min_stress_mpa, max_stress_node, max_stress_mpa = stress_data
            min_disp_node, min_disp_mm, max_disp_node, max_disp_mm = disp_data

            # Engineering sanity checks
            sanity_passed = (max_stress_mpa > 0.0) and (max_disp_mm > 0.0)
            warnings = []
            if max_stress_mpa <= 0.0:
                warnings.append("Maximum stress is zero or negative; check applied loads.")
            if max_disp_mm <= 0.0:
                warnings.append("Maximum displacement is zero; check fixtures.")

            return self._result(
                True,
                f"Extracted simulation results for '{study.Name}'.",
                SwErrors.swSuccess,
                {
                    "study_name": study.Name,
                    "von_mises_stress_mpa": {
                        "max": round(float(max_stress_mpa), 4),
                        "max_node": int(max_stress_node),
                        "min": round(float(min_stress_mpa), 6),
                        "min_node": int(min_stress_node),
                        "unit": "MPa",
                    },
                    "resultant_displacement_mm": {
                        "max": round(float(max_disp_mm), 6),
                        "max_node": int(max_disp_node),
                        "min": round(float(min_disp_mm), 8),
                        "min_node": int(min_disp_node),
                        "unit": "mm",
                    },
                    "sanity_check": {
                        "passed": sanity_passed,
                        "warnings": warnings,
                    }
                }
            )
        except Exception as e:
            logger.error(f"get_simulation_results exception: {e}")
            return self._result(False, f"Exception extracting results: {e}", SwErrors.swSimulationError)
