"""
Automated Model Verification Engine
-----------------------------------
Verifies the integrity, rebuild status, topology, and dimensional accuracy
of SolidWorks models following geometry mutations.
Produces structured VerificationReport objects.
"""

import math
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class DimensionCheck:
    """Comparison of actual vs expected bounding box dimensions."""
    expected_mm: Dict[str, float]
    actual_mm: Dict[str, float]
    diffs_mm: Dict[str, float]
    tolerance_mm: float
    passed: bool


@dataclass
class VerificationReport:
    """Comprehensive geometric and rebuild verification report."""
    passed: bool
    rebuild_clean: bool
    rebuild_error_count: int
    body_count: int
    dimensions_mm: Dict[str, float]
    volume_mm3: float
    surface_area_mm2: float
    center_of_mass_mm: List[float]
    dimension_check: Optional[DimensionCheck] = None
    warnings: List[str] = field(default_factory=list)
    rebuild_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "passed": self.passed,
            "rebuild_clean": self.rebuild_clean,
            "rebuild_error_count": self.rebuild_error_count,
            "body_count": self.body_count,
            "dimensions_mm": self.dimensions_mm,
            "volume_mm3": round(self.volume_mm3, 2),
            "surface_area_mm2": round(self.surface_area_mm2, 2),
            "center_of_mass_mm": [round(c, 3) for c in self.center_of_mass_mm],
            "warnings": self.warnings,
            "rebuild_errors": self.rebuild_errors,
        }
        if self.dimension_check:
            out["dimension_check"] = {
                "passed": self.dimension_check.passed,
                "expected_mm": self.dimension_check.expected_mm,
                "actual_mm": self.dimension_check.actual_mm,
                "diffs_mm": self.dimension_check.diffs_mm,
                "tolerance_mm": self.dimension_check.tolerance_mm,
            }
        return out


class ModelVerifier:
    """Extracts measurements and evaluates model health and dimensional invariants."""

    @classmethod
    def verify(
        cls,
        doc: Any,
        expected_dimensions: Optional[Dict[str, float]] = None,
        expected_bodies: Optional[int] = None,
        tolerance_mm: float = 0.2,
    ) -> VerificationReport:
        """
        Run full verification suite on active model.
        """
        warnings: List[str] = []
        rebuild_errors: List[str] = []

        if doc is None:
            return VerificationReport(
                passed=False,
                rebuild_clean=False,
                rebuild_error_count=1,
                body_count=0,
                dimensions_mm={"dx": 0.0, "dy": 0.0, "dz": 0.0},
                volume_mm3=0.0,
                surface_area_mm2=0.0,
                center_of_mass_mm=[0.0, 0.0, 0.0],
                warnings=["No active document available for verification."],
            )

        # 1. Force rebuild
        try:
            rebuild = getattr(doc, "EditRebuild3", None)
            if callable(rebuild):
                rebuild()
        except Exception as e:
            warnings.append(f"EditRebuild3 warning: {e}")

        # 2. Check rebuild error count
        rebuild_count = 0
        try:
            get_err_cnt = getattr(doc, "GetRebuildErrorCount", None)
            if get_err_cnt:
                rebuild_count = get_err_cnt() if callable(get_err_cnt) else get_err_cnt
        except Exception as e:
            logger.debug(f"GetRebuildErrorCount check: {e}")

        if rebuild_count > 0:
            rebuild_errors.append(f"Model has {rebuild_count} rebuild errors.")

        # 3. Check bodies
        bodies = None
        try:
            bodies = doc.GetBodies2(0, False)
        except Exception as e:
            warnings.append(f"GetBodies2 error: {e}")

        body_count = len(bodies) if bodies else 0
        if body_count == 0:
            warnings.append("No solid bodies found in model.")

        if expected_bodies is not None and body_count != expected_bodies:
            warnings.append(f"Expected {expected_bodies} solid bodies, but found {body_count}.")

        # 4. Compute bounding box
        dimensions_mm = {"dx": 0.0, "dy": 0.0, "dz": 0.0}
        min_pt = [float("inf"), float("inf"), float("inf")]
        max_pt = [float("-inf"), float("-inf"), float("-inf")]

        if bodies:
            for body in bodies:
                try:
                    box_prop = getattr(body, "GetBodyBox", None)
                    box = box_prop() if callable(box_prop) else box_prop
                    if box and len(box) >= 6:
                        min_pt[0] = min(min_pt[0], box[0])
                        min_pt[1] = min(min_pt[1], box[1])
                        min_pt[2] = min(min_pt[2], box[2])
                        max_pt[0] = max(max_pt[0], box[3])
                        max_pt[1] = max(max_pt[1], box[4])
                        max_pt[2] = max(max_pt[2], box[5])
                except Exception as e:
                    logger.debug(f"BodyBox query warning: {e}")

            if min_pt[0] != float("inf"):
                dimensions_mm["dx"] = round((max_pt[0] - min_pt[0]) * 1000.0, 3)
                dimensions_mm["dy"] = round((max_pt[1] - min_pt[1]) * 1000.0, 3)
                dimensions_mm["dz"] = round((max_pt[2] - min_pt[2]) * 1000.0, 3)

        # 5. Mass properties
        volume_mm3 = 0.0
        surface_area_mm2 = 0.0
        com_mm = [0.0, 0.0, 0.0]

        try:
            # Method 1: doc.GetMassProperties (returns 12 doubles: COM x,y,z, Volume, SurfaceArea, Mass, ...)
            get_mp = getattr(doc, "GetMassProperties", None)
            mp_arr = None
            if callable(get_mp):
                try:
                    mp_arr = get_mp()
                except Exception:
                    pass
            elif mp_arr is None and get_mp is not None and not callable(get_mp):
                mp_arr = get_mp

            if mp_arr and len(mp_arr) >= 5:
                com_mm = [
                    round(float(mp_arr[0]) * 1000.0, 3),
                    round(float(mp_arr[1]) * 1000.0, 3),
                    round(float(mp_arr[2]) * 1000.0, 3),
                ]
                volume_mm3 = float(mp_arr[3]) * 1e9
                surface_area_mm2 = float(mp_arr[4]) * 1e6
            else:
                # Method 2: doc.Extension.CreateMassProperty fallback
                ext = getattr(doc, "Extension", None)
                if ext:
                    mp = None
                    for mp_name in ["CreateMassProperty2", "CreateMassProperty"]:
                        mp_fn = getattr(ext, mp_name, None)
                        if callable(mp_fn):
                            try:
                                mp = mp_fn()
                                if mp:
                                    break
                            except Exception:
                                pass

                    if mp:
                        vol_m3 = getattr(mp, "Volume", 0.0)
                        if callable(vol_m3):
                            vol_m3 = vol_m3()
                        volume_mm3 = vol_m3 * 1e9 if vol_m3 else 0.0

                        sa_m2 = getattr(mp, "SurfaceArea", 0.0)
                        if callable(sa_m2):
                            sa_m2 = sa_m2()
                        surface_area_mm2 = sa_m2 * 1e6 if sa_m2 else 0.0

                        com_raw = getattr(mp, "CenterOfMass", None)
                        if callable(com_raw):
                            com_raw = com_raw()
                        if com_raw and len(com_raw) >= 3:
                            com_mm = [round(float(c) * 1000.0, 3) for c in com_raw[:3]]
        except Exception as e:
            logger.debug(f"MassProperty query error: {e}")

        # 6. Compare with expected dimensions
        dim_check = None
        dim_passed = True
        if expected_dimensions:
            diffs = {}
            exact_axis_passed = True
            for axis in ("dx", "dy", "dz"):
                exp_val = expected_dimensions.get(axis) or expected_dimensions.get(axis.upper()) or expected_dimensions.get(axis[1])
                if exp_val is not None:
                    act_val = dimensions_mm.get(axis, 0.0)
                    diff = abs(act_val - float(exp_val))
                    diffs[axis] = round(diff, 3)
                    if diff > tolerance_mm:
                        exact_axis_passed = False

            # Also check rotation-invariant sorted principal dimensions
            exp_vals = [float(v) for k, v in expected_dimensions.items() if k.lower() in ("dx", "dy", "dz", "x", "y", "z") and v is not None]
            act_vals = [dimensions_mm["dx"], dimensions_mm["dy"], dimensions_mm["dz"]]

            principal_matched = False
            if len(exp_vals) == 3:
                exp_sorted = sorted(exp_vals)
                act_sorted = sorted(act_vals)
                principal_matched = all(abs(e - a) <= tolerance_mm for e, a in zip(exp_sorted, act_sorted))

            dim_passed = exact_axis_passed or principal_matched
            if not dim_passed:
                warnings.append(
                    f"Dimension mismatch: actual {dimensions_mm} vs expected {expected_dimensions} (tolerance: {tolerance_mm} mm)"
                )

            dim_check = DimensionCheck(
                expected_mm=expected_dimensions,
                actual_mm=dimensions_mm,
                diffs_mm=diffs,
                tolerance_mm=tolerance_mm,
                passed=dim_passed,
            )

        passed = (rebuild_count == 0) and (body_count > 0) and dim_passed

        return VerificationReport(
            passed=passed,
            rebuild_clean=(rebuild_count == 0),
            rebuild_error_count=rebuild_count,
            body_count=body_count,
            dimensions_mm=dimensions_mm,
            volume_mm3=volume_mm3,
            surface_area_mm2=surface_area_mm2,
            center_of_mass_mm=com_mm,
            dimension_check=dim_check,
            warnings=warnings,
            rebuild_errors=rebuild_errors,
        )
