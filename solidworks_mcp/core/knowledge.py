"""
Knowledge Management Engine
---------------------------
Provides structured, versioned storage and retrieval for SOLIDWORKS API quirks,
verified behaviors, failure modes, and evidence records.
Supports promotion states from 'discovered' to 'confirmed'.
"""

import os
import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


class PromotionState(str, Enum):
    """Knowledge record promotion states."""
    DISCOVERED = "discovered"
    EXPERIMENTALLY_VERIFIED = "experimentally_verified"
    DOCUMENTED = "documented"
    CONFIRMED = "confirmed"
    VERSION_SPECIFIC = "version_specific"


@dataclass
class KnowledgeRecord:
    """Structured evidence and behavior record for SolidWorks API."""
    id: str
    title: str
    date: str
    product_version: str
    api_interface: str
    api_member: str
    problem: str
    attempted_approaches: List[str]
    successful_approach: str
    source: str
    confidence: float  # 0.0 to 1.0
    verification_method: str
    promotion_state: PromotionState
    tags: List[str] = field(default_factory=list)
    evidence_notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["promotion_state"] = self.promotion_state.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgeRecord":
        copy_data = dict(data)
        if isinstance(copy_data.get("promotion_state"), str):
            copy_data["promotion_state"] = PromotionState(copy_data["promotion_state"])
        return cls(**copy_data)


class KnowledgeManager:
    """Manages versioned knowledge storage across promotion tiers."""

    def __init__(self, base_dir: Optional[Path] = None):
        if base_dir is None:
            # Default to repo root / knowledge
            project_root = Path(__file__).resolve().parent.parent.parent
            base_dir = project_root / "knowledge"

        self.base_dir = Path(base_dir)
        self._ensure_directories()

    def _ensure_directories(self):
        """Create tier directories if they do not exist."""
        for state in PromotionState:
            tier_dir = self.base_dir / state.value
            tier_dir.mkdir(parents=True, exist_ok=True)

    def _get_record_path(self, record_id: str, state: PromotionState) -> Path:
        return self.base_dir / state.value / f"{record_id}.json"

    def save_record(self, record: KnowledgeRecord) -> str:
        """Save a knowledge record to its appropriate tier directory."""
        self._ensure_directories()
        path = self._get_record_path(record.id, record.promotion_state)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"Saved knowledge record '{record.id}' to {path}")
        return str(path)

    def get_record(self, record_id: str) -> Optional[KnowledgeRecord]:
        """Find and load a knowledge record across all tiers."""
        for state in PromotionState:
            path = self._get_record_path(record_id, state)
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return KnowledgeRecord.from_dict(data)
                except Exception as e:
                    logger.error(f"Error loading record from {path}: {e}")
        return None

    def promote_record(
        self,
        record_id: str,
        target_state: PromotionState,
        evidence_note: str,
        new_confidence: Optional[float] = None,
    ) -> bool:
        """Promote or move a knowledge record to a new tier with added evidence."""
        record = self.get_record(record_id)
        if not record:
            logger.warning(f"Record '{record_id}' not found for promotion.")
            return False

        old_path = self._get_record_path(record.id, record.promotion_state)

        # Update record
        record.promotion_state = target_state
        if new_confidence is not None:
            record.confidence = max(0.0, min(1.0, new_confidence))
        if evidence_note:
            prior = f"{record.evidence_notes}\n" if record.evidence_notes else ""
            record.evidence_notes = f"{prior}[Promotion to {target_state.value}]: {evidence_note}"

        # Save to new location
        new_path = self._get_record_path(record.id, target_state)
        with open(new_path, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, indent=2, ensure_ascii=False)

        # Remove old file if path changed
        if old_path.exists() and old_path != new_path:
            old_path.unlink()

        logger.info(f"Promoted record '{record_id}' from {old_path} to {new_path}")
        return True

    def list_records(
        self,
        tier: Optional[PromotionState] = None,
    ) -> List[KnowledgeRecord]:
        """List all records, optionally filtered by tier."""
        records = []
        states_to_check = [tier] if tier else list(PromotionState)
        for state in states_to_check:
            tier_dir = self.base_dir / state.value
            if tier_dir.exists():
                for file_path in tier_dir.glob("*.json"):
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        records.append(KnowledgeRecord.from_dict(data))
                    except Exception as e:
                        logger.error(f"Failed to read {file_path}: {e}")
        return records

    def search(
        self,
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
        interface: Optional[str] = None,
        min_confidence: float = 0.0,
    ) -> List[KnowledgeRecord]:
        """Search records by keyword, tag, interface, or minimum confidence."""
        results = []
        q_lower = query.lower() if query else None
        tag_set = {t.lower() for t in tags} if tags else None
        iface_lower = interface.lower() if interface else None

        for rec in self.list_records():
            if rec.confidence < min_confidence:
                continue

            if iface_lower and iface_lower not in rec.api_interface.lower():
                continue

            if tag_set:
                rec_tags = {t.lower() for t in rec.tags}
                if not tag_set.issubset(rec_tags) and not tag_set.intersection(rec_tags):
                    continue

            if q_lower:
                match_text = f"{rec.title} {rec.problem} {rec.successful_approach} {rec.api_member}".lower()
                if q_lower not in match_text:
                    continue

            results.append(rec)

        return results

    def seed_default_knowledge(self) -> int:
        """Seed verified knowledge records discovered during development phases."""
        seeds = [
            KnowledgeRecord(
                id="sw2025_cdispatch_callable_loop_halt",
                title="CDispatch Objects Are Always Callable in win32com",
                date="2026-09-30",
                product_version="33.5.0.53 (SOLIDWORKS 2025 SP5)",
                api_interface="IFeature / CDispatch",
                api_member="GetNextFeature / callable()",
                problem="callable(feat) returns True for all win32com CDispatch instances due to DISPID_VALUE implementation. Calling feat() raises (-2147352573, Member not found) and halts loops.",
                attempted_approaches=["feat = feat.GetNextFeature; if callable(feat): feat = feat()"],
                successful_approach="In SW 2025 COM, GetNextFeature is a property returning the next IFeature directly. Iterate cleanly with 'feat = feat.GetNextFeature' without checking callable().",
                source="live_experiment",
                confidence=1.0,
                verification_method="live_acceptance_test",
                promotion_state=PromotionState.CONFIRMED,
                tags=["win32com", "feature_tree", "com_dispatch", "traversal"],
                evidence_notes="Verified in Phase 0 audit and Phase 2 testing. Traverses full feature trees reliably."
            ),
            KnowledgeRecord(
                id="sw2025_saveas3_viewport_capture",
                title="Direct High-Resolution Viewport Capture via SaveAs3",
                date="2026-09-30",
                product_version="33.5.0.53 (SOLIDWORKS 2025 SP5)",
                api_interface="IModelDoc2",
                api_member="SaveAs3",
                problem="External screen scrapers or clipboard grabs are non-deterministic and require active window focus.",
                attempted_approaches=["Windows GDI BitBlt clipboard grab", "ModelView.GetGraphicsData"],
                successful_approach="Call doc.SaveAs3(filepath, 0, 2) with .png or .jpg extension (version 0 = swSaveAsCurrentVersion, options 2 = swSaveAsOptions_Silent). Returns 0 on success.",
                source="live_experiment",
                confidence=1.0,
                verification_method="live_acceptance_test",
                promotion_state=PromotionState.CONFIRMED,
                tags=["viewport", "export", "rendering", "screenshots"],
                evidence_notes="Verified in Phases 2, 3, and 4 generating 43 KB to 56 KB high-resolution renders."
            ),
            KnowledgeRecord(
                id="sw2025_face_surface_properties",
                title="Face and Surface Geometric Properties in SW 2025",
                date="2026-09-30",
                product_version="33.5.0.53 (SOLIDWORKS 2025 SP5)",
                api_interface="IFace2 / ISurface",
                api_member="GetSurface / IsPlane / PlaneParams / GetBox",
                problem="Invoking face.GetSurface() or surf.PlaneParams() as methods throws (-2147352573, Member not found).",
                attempted_approaches=["face.GetSurface()", "surf.PlaneParams()"],
                successful_approach="Access face.GetSurface, surf.IsPlane, surf.PlaneParams, and face.GetBox as COM properties. PlaneParams returns (nx, ny, nz, d, root_x, root_y).",
                source="live_experiment",
                confidence=1.0,
                verification_method="live_acceptance_test",
                promotion_state=PromotionState.CONFIRMED,
                tags=["geometry", "selection", "surfaces", "planes", "win32com"],
                evidence_notes="Verified in DeclarativeSelectionResolver across multiple test assemblies and parts."
            ),
            KnowledgeRecord(
                id="sw2025_interference_detection_done",
                title="InterferenceDetectionManager Requires Done() to Unlock Selections",
                date="2026-09-30",
                product_version="33.5.0.53 (SOLIDWORKS 2025 SP5)",
                api_interface="IAssemblyDoc / IInterferenceDetectionManager",
                api_member="InterferenceDetectionManager / Done",
                problem="Accessing doc.InterferenceDetectionManager opens the interactive PropertyManager page, which blocks all subsequent entity selections (Select2 returns False).",
                attempted_approaches=["Ignoring the PropertyManager page", "doc.ClearSelection2(True)"],
                successful_approach="Always invoke int_mgr.Done() immediately after reading interference results to close the PropertyManager page and restore selection capabilities.",
                source="live_experiment",
                confidence=1.0,
                verification_method="live_acceptance_test",
                promotion_state=PromotionState.CONFIRMED,
                tags=["assembly", "interference", "propertymanager", "selection"],
                evidence_notes="Verified in Phase 3 assembly tests. Unblocks subsequent mating and selection."
            ),
            KnowledgeRecord(
                id="sw2025_simulation_cosmosworks_addin",
                title="Simulation Add-in Loading and Interface Navigation",
                date="2026-10-01",
                product_version="33.5.0.53 (SOLIDWORKS 2025 SP5)",
                api_interface="ISldWorks / COSMOSWorks",
                api_member="LoadAddIn / GetAddInObject",
                problem="GetAddInObject('SldWorks.SimulationAddin') returns None.",
                attempted_approaches=["sw.GetAddInObject('SldWorks.SimulationAddin')", "sw.GetAddInObject('SldWorks.Simulation')"],
                successful_approach="Load dll via sw.LoadAddIn(r'C:\\Program Files\\SOLIDWORKS Corp\\SOLIDWORKS\\Simulation\\cosworks.dll'), then retrieve addin = sw.GetAddInObject('CosmosWorks.CosmosWorks'). Root object is addin.COSMOSWORKS.",
                source="cosworks.tlb & live_experiment",
                confidence=1.0,
                verification_method="live_acceptance_test",
                promotion_state=PromotionState.CONFIRMED,
                tags=["simulation", "fea", "add_in", "cosmosworks"],
                evidence_notes="Verified in Phase 4 simulation investigations and automated live runs."
            ),
            KnowledgeRecord(
                id="sw2025_simulation_null_dispatch_refgeom",
                title="Simulation Restraint/Load Reference Geometry Null Dispatch Requirement",
                date="2026-10-01",
                product_version="33.5.0.53 (SOLIDWORKS 2025 SP5)",
                api_interface="ICWLoadsAndRestraintsManager",
                api_member="AddRestraint / AddPressure / AddForce",
                problem="Passing Python None for RefGeom raises pywintypes.com_error: Type mismatch (parameter 3).",
                attempted_approaches=["lrm.AddRestraint(0, [face], None, err_var)", "lrm.AddRestraint(0, [face], 0, err_var)"],
                successful_approach="Wrap null reference geometry in explicit COM dispatch variant: win32com.client.VARIANT(pythoncom.VT_DISPATCH, None).",
                source="live_experiment",
                confidence=1.0,
                verification_method="live_acceptance_test",
                promotion_state=PromotionState.CONFIRMED,
                tags=["simulation", "win32com", "byref", "variants", "fixtures", "loads"],
                evidence_notes="Verified in Phase 4. Completely eliminated Type Mismatch errors."
            ),
            KnowledgeRecord(
                id="sw2025_simulation_runanalysis_property",
                title="Simulation Solver Execution is a Property Getter in win32com",
                date="2026-10-01",
                product_version="33.5.0.53 (SOLIDWORKS 2025 SP5)",
                api_interface="ICWStudy",
                api_member="RunAnalysis",
                problem="Calling study.RunAnalysis() throws TypeError: 'int' object is not callable.",
                attempted_approaches=["study.RunAnalysis()", "study.RunAnalysis(err_var)"],
                successful_approach="In win32com, RunAnalysis is exposed as a property getter. Simply evaluating 'err = study.RunAnalysis' synchronously solves the FEA model and returns 0 on success.",
                source="live_experiment",
                confidence=1.0,
                verification_method="live_acceptance_test",
                promotion_state=PromotionState.CONFIRMED,
                tags=["simulation", "fea", "solver", "runanalysis", "win32com"],
                evidence_notes="Verified in Phase 4 live cantilever test (solved 9,844 elements in 6.5s)."
            ),
            KnowledgeRecord(
                id="sw2025_simulation_edit_properties",
                title="PressureEndEdit and ForceEndEdit Are Property Getters",
                date="2026-10-01",
                product_version="33.5.0.53 (SOLIDWORKS 2025 SP5)",
                api_interface="ICWPressure / ICWForce",
                api_member="PressureEndEdit / ForceEndEdit",
                problem="Calling pres.PressureEndEdit() raises TypeError: 'int' object is not callable.",
                attempted_approaches=["pres.PressureEndEdit()", "force.ForceEndEdit()"],
                successful_approach="Evaluate as property: 'err = getattr(pres, \"PressureEndEdit\", 0)' or 'pres.PressureEndEdit'. Commits the edit and returns 0 on success.",
                source="live_experiment",
                confidence=1.0,
                verification_method="live_acceptance_test",
                promotion_state=PromotionState.CONFIRMED,
                tags=["simulation", "pressure", "force", "loads", "win32com"],
                evidence_notes="Verified in Phase 4 live load application."
            ),
            KnowledgeRecord(
                id="sw2025_simulation_result_extrema_tuples",
                title="Simulation Stress and Displacement Extrema Tuple Signatures",
                date="2026-10-01",
                product_version="33.5.0.53 (SOLIDWORKS 2025 SP5)",
                api_interface="ICWResults",
                api_member="GetMinMaxStress / GetMinMaxDisplacement",
                problem="Uncertain return layout and unit scaling from results extraction methods.",
                attempted_approaches=["results.GetStressComponent", "results.GetDisplacementComponent"],
                successful_approach="Call results.GetMinMaxStress(9, 0, 1, null_dispatch, 3, err) for von Mises in MPa; returns (min_node, min_val, max_node, max_val). Call results.GetMinMaxDisplacement(3, 1, null_dispatch, 0, err) for resultant in mm; returns (min_node, min_val, max_node, max_val).",
                source="cosworks.tlb & live_experiment",
                confidence=1.0,
                verification_method="live_acceptance_test",
                promotion_state=PromotionState.CONFIRMED,
                tags=["simulation", "results", "stress", "displacement", "extrema"],
                evidence_notes="Verified in Phase 4 extracting exact nodal stresses and displacements."
            ),
        ]

        saved_count = 0
        for s in seeds:
            self.save_record(s)
            saved_count += 1
        return saved_count


_knowledge_manager: Optional[KnowledgeManager] = None

def get_knowledge_manager() -> KnowledgeManager:
    global _knowledge_manager
    if _knowledge_manager is None:
        _knowledge_manager = KnowledgeManager()
        _knowledge_manager.seed_default_knowledge()
    return _knowledge_manager
