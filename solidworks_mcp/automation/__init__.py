"""
SolidWorks Automation Package
-----------------------------
Complete automation class combining all operations.
"""

from .base import (
    SolidWorksAutomation as _BaseAutomation,
    get_process_details,
    get_running_app,
    launch_solidworks,
    find_sldworks_pids,
)
from .documents import DocumentOperations
from .sketches import SketchOperations
from .features import FeatureOperations
from .inspection import InspectionOperations
from .assemblies import AssemblyOperations
from .drawings import DrawingOperations
from .simulation import SimulationOperations
from .diagnostics import DiagnosticOperations


class SolidWorksAutomation(_BaseAutomation, DocumentOperations, 
                           SketchOperations, FeatureOperations,
                           InspectionOperations, AssemblyOperations,
                           DrawingOperations, SimulationOperations,
                           DiagnosticOperations):
    """
    Complete SolidWorks automation class
    
    Combines all operation mixins:
    - Base: Connection, document access, utilities, STA COM worker
    - Documents: Create, open, save, close documents
    - Sketches: Create sketches, draw 2D geometry
    - Features: Extrude, cut, fillet, chamfer
    - Inspection: Geometry inspection, verification, selection, viewport capture
    - Assemblies: Component insertion, mates, interference detection, component listing
    - Drawings: Drawing creation from models, views, and PDF/DXF/DWG export
    - Simulation: FEA studies, materials, fixtures, loads, meshing, solver, results
    - Diagnostics: Dialog inspection, dismissal, dynamic add-ins, rebuild error checks
    """
    pass


__all__ = [
    "SolidWorksAutomation",
    "get_running_app",
    "launch_solidworks",
    "get_process_details",
    "find_sldworks_pids",
    "DocumentOperations",
    "SketchOperations", 
    "FeatureOperations",
    "InspectionOperations",
    "AssemblyOperations",
    "DrawingOperations",
    "SimulationOperations",
    "DiagnosticOperations",
]
