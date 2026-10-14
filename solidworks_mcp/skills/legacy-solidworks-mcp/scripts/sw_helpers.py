r"""
SolidWorks MCP Automation Helper Utilities (sw_helpers)
=======================================================
A battle-tested reusable library for SolidWorks COM API automation, geometry modeling,
material assignment, and SolidWorks Simulation (COSMOSWorks) setup/solving.

Designed to be imported directly inside SolidWorks MCP `execute_python` calls:
    import sys
    sys.path.insert(0, r"str(Path(__file__).resolve().parent)")
    import sw_helpers as swh
"""

import sys
import os
import math
import traceback
from typing import List, Dict, Tuple, Optional, Any, Union

import win32com.client
import pythoncom


# ==============================================================================
# 1. COM Type Helpers & Safe Property/Method Access
# ==============================================================================

def get_null_dispatch():
    """Returns a properly wrapped null IDispatch VARIANT for SolidWorks COM calls."""
    return win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

def create_byref_int(initial: int = 0):
    """Returns a ByRef Integer VARIANT (VT_BYREF | VT_I4) for COM out parameters."""
    return win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, initial)

def get_prop_or_call(obj: Any, name: str, *args, **kwargs):
    """
    Safely accesses an attribute that might be either a property or a callable method
    depending on SolidWorks COM wrapper version.
    """
    if not hasattr(obj, name):
        raise AttributeError(f"Object {obj} has no attribute '{name}'")
    val = getattr(obj, name)
    if callable(val):
        try:
            return val(*args, **kwargs)
        except TypeError:
            # Might be property misidentified as callable
            return val
    return val


# ==============================================================================
# 2. Document & View Utilities
# ==============================================================================

def get_active_title(doc: Any) -> str:
    """Returns the title of the active document (handling property vs method)."""
    return str(get_prop_or_call(doc, "GetTitle"))

def zoom_to_fit(doc: Any) -> None:
    """Zooms to fit and redraws active graphics window."""
    try:
        doc.ViewZoomtofit2()
        doc.GraphicsRedraw2()
    except Exception as e:
        print(f"[sw_helpers] zoom_to_fit warning: {e}")

def save_document(doc: Any, filepath: Optional[str] = None) -> bool:
    """
    Safely saves the active document using ByRef error/warning variants.
    If filepath is provided, performs SaveAs3; otherwise performs Save3.
    """
    err = create_byref_int()
    warn = create_byref_int()
    if filepath:
        # SaveAs3: Options=0 (swSaveAsCurrentVersion), Copy=2 (swSaveAsCopy) or 0
        res = doc.SaveAs3(filepath, 0, 2)
        success = (res == 0 or res is True)
        if not success:
            print(f"[sw_helpers] SaveAs3 failed: code={res}")
        return success
    else:
        # Save3: 1 = swSaveAsCurrentVersion
        res = doc.Save3(1, err, warn)
        return bool(res)


# ==============================================================================
# 3. Selection & Sketching Helpers
# ==============================================================================

def select_by_id(doc: Any, name: str, obj_type: str, x: float = 0.0, y: float = 0.0,
                 z: float = 0.0, append: bool = False, mark: int = 0) -> bool:
    """
    Safe wrapper for doc.Extension.SelectByID2 with null dispatch callout.
    Avoids 'Type mismatch (error 8)' when passing None.
    """
    null_disp = get_null_dispatch()
    return bool(doc.Extension.SelectByID2(name, obj_type, x, y, z, append, mark, null_disp, 0))

def get_sketch_segments(sketch_feat: Any) -> list:
    """Returns list/tuple of sketch segments from a sketch feature."""
    sk = get_prop_or_call(sketch_feat, "GetSpecificFeature2")
    return get_prop_or_call(sk, "GetSketchSegments")


# ==============================================================================
# 4. Feature Extrusion (SW 2025 23-param safe)
# ==============================================================================

def extrude_boss(doc: Any, depth_m: float, both_directions: bool = False,
                 merge: bool = True) -> Any:
    """
    Executes FeatureExtrusion2 with exact 23 parameters compatible with SW 2025 / v33.
    depth_m: depth in meters.
    both_directions: if True, end condition is 6 (MidPlane); otherwise 0 (Blind).
    merge: True to merge solid result, False to create independent body.
    """
    end_cond = 6 if both_directions else 0
    feat = doc.FeatureManager.FeatureExtrusion2(
        True,           # Sd (single direction)
        False,          # Flip
        False,          # Dir
        end_cond,       # T1 (0=Blind, 6=MidPlane)
        0,              # T2
        depth_m,        # D1
        depth_m,        # D2
        False,          # Dchk1
        False,          # Dchk2
        False,          # Ddir1
        False,          # Ddir2
        0.0,            # Dang1
        0.0,            # Dang2
        False,          # OffsetReverse1
        False,          # OffsetReverse2
        False,          # TranslateSurface1
        False,          # TranslateSurface2
        merge,          # Merge
        True,           # UseFeatScope
        True,           # UseAutoSelect
        0,              # T0
        0.0,            # StartOffset
        False           # FlipStartOffset
    )
    return feat


# ==============================================================================
# 5. Inspection (Bodies & Faces)
# ==============================================================================

def get_bodies(doc: Any, body_type: int = 0) -> list:
    """
    Returns list of bodies. body_type: 0 = swSolidBody, 1 = swSheetBody.
    """
    bodies = doc.GetBodies2(body_type, False)
    return list(bodies) if bodies else []

def get_body_bounding_box_mm(body: Any) -> Dict[str, float]:
    """Returns bounding box dimensions in mm and min/max coordinates."""
    box = get_prop_or_call(body, "GetBodyBox")
    return {
        "minX_mm": box[0] * 1000.0,
        "minY_mm": box[1] * 1000.0,
        "minZ_mm": box[2] * 1000.0,
        "maxX_mm": box[3] * 1000.0,
        "maxY_mm": box[4] * 1000.0,
        "maxZ_mm": box[5] * 1000.0,
        "dx_mm": (box[3] - box[0]) * 1000.0,
        "dy_mm": (box[4] - box[1]) * 1000.0,
        "dz_mm": (box[5] - box[2]) * 1000.0,
    }

def get_face_details(face: Any) -> Dict[str, Any]:
    """Extracts area (m^2), bounding box (m), and center point of a face."""
    area = get_prop_or_call(face, "GetArea")
    box = get_prop_or_call(face, "GetBox")
    cx = (box[0] + box[3]) / 2.0
    cy = (box[1] + box[4]) / 2.0
    cz = (box[2] + box[5]) / 2.0
    return {
        "area_m2": area,
        "box": box,
        "center": (cx, cy, cz),
    }


# ==============================================================================
# 6. Material Assignment
# ==============================================================================

def assign_material(doc: Any, material_name: str,
                    database: str = "solidworks materials.sldmat",
                    config: str = "Default") -> str:
    """
    Assigns material to the active document configuration.
    Example material_name: "ASTM A36 Steel", "Alloy Steel", "AISI 1020".
    Note: Requires at least one solid/surface body in the part to avoid COM RPC_E_SERVERFAULT.
    """
    try:
        doc.SetMaterialPropertyName2(config, database, material_name)
        return str(get_prop_or_call(doc, "MaterialIdName"))
    except Exception as e:
        print(f"[sw_helpers] assign_material warning: {e}")
        return "Not Assigned"


# ==============================================================================
# 7. SolidWorks Simulation Automation (COSMOSWorks)
# ==============================================================================

def get_cosmosworks(sw: Any) -> Any:
    """Retrieves the CosmosWorks automation object from SolidWorks."""
    sim_addin = sw.GetAddInObject("SldWorks.Simulation")
    if not sim_addin:
        raise RuntimeError("SolidWorks Simulation add-in is not loaded or not licensed.")
    return sim_addin.CosmosWorks

def create_static_study(sw: Any, study_name: str = "Static_Study",
                        mesh_type: int = 0) -> Any:
    """
    Creates a new Static Simulation study in active document.
    mesh_type: 0 = Solid Mesh, 1 = MidSurface, 2 = Beams/Trusses.
    """
    cosmos = get_cosmosworks(sw)
    act_doc = cosmos.ActiveDoc
    if not act_doc:
        raise RuntimeError("No active document in Simulation manager.")
    study_mgr = act_doc.StudyManager
    err_var = create_byref_int()
    study = study_mgr.CreateNewStudy3(study_name, 0, mesh_type, err_var)
    if not study or err_var.value != 0:
        raise RuntimeError(f"CreateNewStudy3 failed with error code: {err_var.value}")
    return study

def add_fixed_restraint(study: Any, faces: List[Any],
                        restraint_name: Optional[str] = None) -> Any:
    """
    Adds a Fixed Geometry restraint (swsRestraintTypeFixed = 0) to specified faces.
    """
    lr_mgr = study.LoadsAndRestraintsManager
    null_disp = get_null_dispatch()
    err_var = create_byref_int()
    restraint = lr_mgr.AddRestraint(0, faces, null_disp, err_var)
    if err_var.value != 0:
        raise RuntimeError(f"AddRestraint failed with error code: {err_var.value}")
    return restraint

def add_normal_force(study: Any, faces: List[Any], force_value: float,
                     unit: int = 0, is_tension: bool = True) -> Any:
    """
    Adds normal force to specified faces.
    unit: 0 = N, 1 = lb (lbf), 2 = kgf.
    is_tension: In SolidWorks, positive normal force acts inward (compression).
                Setting is_tension=True applies -force_value to pull outward.
    """
    lr_mgr = study.LoadsAndRestraintsManager
    null_disp = get_null_dispatch()
    err_var = create_byref_int()
    
    # 1 = swsForceTypeNormal
    force_obj = lr_mgr.AddForce(1, faces, null_disp, err_var)
    if not force_obj or err_var.value != 0:
        raise RuntimeError(f"AddForce failed with error code: {err_var.value}")
    
    force_obj.ForceBeginEdit()
    force_obj.Unit = unit
    signed_val = -abs(force_value) if is_tension else abs(force_value)
    force_obj.NormalForceOrTorqueValue = signed_val
    get_prop_or_call(force_obj, "ForceEndEdit")
    return force_obj

def mesh_and_solve(study: Any, elem_size: float, tol: float,
                   unit: int = 0) -> int:
    """
    Creates standard solid mesh and runs analysis.
    unit: 0 = mm, 1 = in.
    Returns 0 on success.
    """
    mesh_res = study.CreateMesh(unit, elem_size, tol)
    if mesh_res != 0:
        raise RuntimeError(f"CreateMesh failed with code: {mesh_res}")
    
    # RunAnalysis property executes the solver in SW 2025
    get_prop_or_call(study, "RunAnalysis")
    return 0

def get_simulation_summary(study: Any, length_unit: int = 0,
                           stress_unit: int = 2) -> Dict[str, Any]:
    """
    Extracts key displacement and stress results from a solved study.
    length_unit: 0 = mm, 1 = in.
    stress_unit: 0 = N/m^2 (Pa), 1 = psi, 2 = N/mm^2 (MPa).
    """
    results = study.Results
    if not results:
        raise RuntimeError("No results found on study. Run analysis first.")
    
    null_disp = get_null_dispatch()
    err_var = create_byref_int()
    
    disps = {}
    for comp, name in enumerate(["UX", "UY", "UZ", "URES"]):
        res = results.GetMinMaxDisplacement(comp, 1, null_disp, length_unit, err_var)
        disps[name] = {
            "min_node": res[0],
            "min_val": res[1],
            "max_node": res[2],
            "max_val": res[3],
        }
    
    # 9 = von Mises
    von = results.GetMinMaxStress(9, 0, 1, null_disp, stress_unit, err_var)
    # 0, 1, 2 = SX, SY, SZ
    sx = results.GetMinMaxStress(0, 0, 1, null_disp, stress_unit, err_var)
    sy = results.GetMinMaxStress(1, 0, 1, null_disp, stress_unit, err_var)
    sz = results.GetMinMaxStress(2, 0, 1, null_disp, stress_unit, err_var)
    
    return {
        "displacements": disps,
        "von_mises": {"min": von[1], "max": von[3]},
        "stress_components": {
            "SX": {"min": sx[1], "max": sx[3]},
            "SY": {"min": sy[1], "max": sy[3]},
            "SZ": {"min": sz[1], "max": sz[3]},
        }
    }
