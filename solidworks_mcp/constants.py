"""
SolidWorks MCP Constants
------------------------
All constants, enums, and error codes for SolidWorks automation.
Audited and verified against official SolidWorks 2025 type library (swconst.tlb).
"""

from enum import IntEnum


# ============================================================================
# Error Codes (1-to-1 Mapping Between Error Meaning and Code)
# ============================================================================

class SwErrors(IntEnum):
    """SolidWorks MCP error codes with unique, un-overlapped semantics."""
    swSuccess = 0                # Operation completed successfully
    swGeneralError = 1           # General internal or unexpected failure
    swFileNotFoundError = 2      # File or path does not exist on disk
    swFileLoadError = 3          # Failed to load, open, or deserialize file in SolidWorks
    swFileSaveError = 4          # Failed to save document to disk
    swInvalidFileType = 5        # Unsupported or illegal file extension
    swInvalidDocumentType = 6    # Operation not supported on active document type (e.g. 3D tool on 2D drawing)
    swInvalidCall = 9            # Invalid operation state or security policy refusal (e.g. close_sketch when no sketch active, execute_python disabled)
    swConnectionError = 100      # Not connected to SolidWorks / RPC unavailable
    swNoActiveDocument = 101     # Operation requires an open document but none is open
    swSketchError = 102          # Sketch creation or sketch editing operation failed (e.g. sketch not active)
    swFeatureError = 103         # Feature generation failed (extrude, cut, fillet, chamfer geometry failure)
    swSelectionError = 104       # Entity selection failed (target face/edge/plane not found or invalid)
    swSolidWorksNotFound = 105   # SolidWorks installation or executable not found
    swTemplateNotFound = 106     # Document template (.prtdot, .asmdot, .drwdot) not found
    swInvalidInput = 107         # Invalid argument, parameter out of bounds, or missing parameter
    swSimulationError = 108      # COSMOSWorks / simulation operation failed or no study exists
    swExportError = 109          # Exporting drawing or model to external format failed
    swRebuildError = 111         # Document or feature failed rebuild / regeneration
    swProcessNotFound = 112      # Process ID not found
    swDimensionNotFound = 113    # Dimension name not found in active document
    swDimensionError = 114       # Dimension modification failed
    swOperationFailed = 115      # Low-level CAD COM API call returned failure
    swOperationTimeout = 116     # Operation timed out waiting for COM call or SolidWorks response
    swTimeoutError = 116         # Alias for backward compatibility
    swModalDialogBlocking = 117  # SolidWorks execution blocked by active modal popup dialog
    swWorkerBusy = 118           # Worker thread busy with earlier abandoned background call
    swInternalError = 998        # Severe internal MCP harness fault
    swUnknownError = 999         # Unclassified unexpected error (deprecated)

    # Canonical aliases to resolve overlaps (1-to-1 mapping)
    swInvalidParameter = 107     # Alias for swInvalidInput
    swInvalidParameters = 107    # Alias for swInvalidInput
    swSelectionFailed = 104      # Alias for swSelectionError


# ============================================================================
# Document Types (swDocumentTypes_e from swconst.tlb)
# ============================================================================

class SwDocumentTypes(IntEnum):
    """SolidWorks document types matching swconst.tlb (swDocumentTypes_e)"""
    swDocNONE = 0
    swDocPART = 1
    swDocASSEMBLY = 2
    swDocDRAWING = 3
    swDocSDM = 4
    swDocLAYOUT = 5
    swDocIMPORTED_PART = 6
    swDocIMPORTED_ASSEMBLY = 7


# ============================================================================
# Feature End Conditions (swEndConditions_e from swconst.tlb)
# ============================================================================

class SwEndConditions(IntEnum):
    """Extrusion end conditions matching swconst.tlb (swEndConditions_e)"""
    swEndCondBlind = 0
    swEndCondThroughAll = 1
    swEndCondThroughNext = 2
    swEndCondUpToVertex = 3
    swEndCondUpToSurface = 4
    swEndCondOffsetFromSurface = 5
    swEndCondMidPlane = 6
    swEndCondUpToBody = 7
    swEndCondThroughAllBoth = 9
    swEndCondUpToSelection = 10
    swEndCondUpToNext = 11


# ============================================================================
# Assembly Mate Types (swMateType_e from swconst.tlb)
# ============================================================================

class SwMateTypes(IntEnum):
    """Assembly mate types matching swconst.tlb (swMateType_e)"""
    swMateCOINCIDENT = 0
    swMateCONCENTRIC = 1
    swMatePERPENDICULAR = 2
    swMatePARALLEL = 3
    swMateTANGENT = 4
    swMateDISTANCE = 5
    swMateANGLE = 6
    swMateUNKNOWN = 7
    swMateSYMMETRIC = 8
    swMateCAMFOLLOWER = 9
    swMateGEAR = 10
    swMateWIDTH = 11             # Correct value from swconst.tlb (was previously misassigned as 18)
    swMateLOCKTOSKETCH = 12
    swMateRACKPINION = 13
    swMateMAXMATES = 14
    swMatePATH = 15
    swMateLOCK = 16
    swMateSCREW = 17
    swMateLINEARCOUPLER = 18
    swMateUNIVERSALJOINT = 19
    swMateCOORDINATE = 20
    swMateSLOT = 21
    swMateHINGE = 22
    swMateSLIDER = 23
    swMatePROFILECENTER = 24
    swMateMAGNETIC = 25


# ============================================================================
# Mate Alignment (swMateAlign_e from swconst.tlb)
# ============================================================================

class SwMateAlign(IntEnum):
    """Assembly mate alignment matching swconst.tlb (swMateAlign_e)"""
    swMateAlignALIGNED = 0
    swMateAlignANTI_ALIGNED = 1
    swMateAlignCLOSEST = 2
    swAlignNONE = 0
    swAlignSAME = 1
    swAlignAGAINST = 2


# ============================================================================
# Dimension Driven State (swDimensionDrivenState_e from swconst.tlb)
# ============================================================================

class SwDimensionDrivenState(IntEnum):
    """Dimension driven state matching swconst.tlb (swDimensionDrivenState_e)"""
    swDimensionDrivenUnknown = 0
    swDimensionDriven = 1
    swDimensionDriving = 2


# ============================================================================
# File Save Errors (swFileSaveError_e from swconst.tlb)
# ============================================================================

class SwFileSaveError(IntEnum):
    """File save error bitflags matching swconst.tlb (swFileSaveError_e)"""
    swGenericSaveError = 1
    swReadOnlySaveError = 2
    swFileNameEmpty = 4
    swFileNameContainsAtSign = 8
    swFileLockError = 16
    swFileSaveFormatNotAvailable = 32
    swFileSaveWithRebuildError = 64
    swFileSaveAsDoNotOverwrite = 128
    swFileSaveAsInvalidFileExtension = 256
    swFileSaveAsNoSelection = 512
    swFileSaveAsBadEDrawingsVersion = 1024
    swFileSaveAsNameExceedsMaxPathLength = 2048
    swFileSaveAsNotSupported = 4096
    swFileSaveRequiresSavingReferences = 8192
    swFileSaveAsDetachedDrawingsNotSupported = 16384


# ============================================================================
# File Load Errors (swFileLoadError_e from swconst.tlb)
# ============================================================================

class SwFileLoadError(IntEnum):
    """File load error bitflags matching swconst.tlb (swFileLoadError_e)"""
    swGenericError = 1
    swFileNotFoundError = 2
    swIdMatchError = 4
    swReadOnlyWarn = 8
    swSharingViolationWarn = 16
    swDrawingANSIUpdateWarn = 32
    swSheetScaleUpdateWarn = 64
    swNeedsRegenWarn = 128
    swBasePartNotLoadedWarn = 256
    swFileAlreadyOpenWarn = 512
    swInvalidFileTypeError = 1024
    swDrawingsOnlyRapidDraftWarn = 2048
    swViewOnlyRestrictions = 4096
    swFutureVersion = 8192
    swViewMissingReferencedConfig = 16384
    swDrawingSFSymbolConvertWarn = 32768
    swFileWithSameTitleAlreadyOpen = 65536
    swLiquidMachineDoc = 131072
    swLowResourcesError = 262144
    swNoDisplayData = 524288
    swAddinInteruptError = 1048576
    swFileRequiresRepairError = 2097152
    swFileCriticalDataRepairError = 4194304
    swApplicationBusy = 8388608
    swConnectedIsOffline = 16777216


# ============================================================================
# Feature Errors (swFeatureError_e from swconst.tlb)
# ============================================================================

class SwFeatureError(IntEnum):
    """Feature error codes matching swconst.tlb (swFeatureError_e)"""
    swFeatureErrorNone = 0
    swFeatureErrorUnknown = 1
    swFeatureErrorFilletNoLoop = 10
    swFeatureErrorFilletNoFace = 11
    swFeatureErrorFilletInvalidRadius = 12
    swFeatureErrorFilletNoEdge = 13
    swFeatureErrorFilletModelGeometry = 14
    swFeatureErrorFilletRadiusTooSmall = 15
    swFeatureErrorFilletCannotExtend = 16
    swFeatureErrorFilletRadiusEliminateElement = 17
    swFeatureErrorFilletRadiusTooBig = 18
    swFeatureErrorExtrusionDisjoint = 30
    swFeatureErrorExtrusionNoEndFound = 31
    swFeatureErrorExtrusionBadGeometricConditions = 32
    swFeatureErrorExtrusionCutContourOpenAndClosed = 33
    swFeatureErrorExtrusionCutContourInvalid = 34
    swFeatureErrorExtrusionOpenCutContourInvalid = 35
    swFeatureErrorExtrusionBossContourOpenAndClosed = 36
    swFeatureErrorExtrusionBossContourInvalid = 37
    swFeatureErrorMateInvalidEdge = 38
    swFeatureErrorMateInvalidFace = 39
    swFeatureErrorMateFailedCreatingSurface = 40
    swFeatureErrorMateInvalidEntity = 41
    swFeatureErrorMateUnknownTangent = 42
    swFeatureErrorMateDanglingGeometry = 43
    swFeatureErrorMateEntityNotLinear = 44
    swFeatureErrorMateEntityFailed = 45
    swFeatureErrorMateOverdefined = 46
    swFeatureErrorMateIlldefined = 47
    swFeatureErrorMateBroken = 48


# ============================================================================
# Delete Selection Options (swDeleteSelectionOptions_e from swconst.tlb)
# ============================================================================

class SwDeleteSelectionOptions(IntEnum):
    """SolidWorks DeleteSelection2 bitmask options (swDeleteSelectionOptions_e from swconst.tlb)"""
    swDelete_Children = 1
    swDelete_Absorbed = 2
    swDelete_Advanced = 4


# ============================================================================
# Plane Names
# ============================================================================

class SwPlanes:
    """Standard SolidWorks plane names"""
    FRONT = "Front Plane"
    TOP = "Top Plane"
    RIGHT = "Right Plane"
    
    @classmethod
    def get(cls, name: str) -> str:
        """Get plane name from short name"""
        plane_map = {
            "front": cls.FRONT,
            "top": cls.TOP,
            "right": cls.RIGHT,
        }
        return plane_map.get(name.lower(), cls.FRONT)
    
    @classmethod
    def all(cls) -> list:
        """Get all plane names"""
        return [cls.FRONT, cls.TOP, cls.RIGHT]


# ============================================================================
# View Types
# ============================================================================

class SwViews:
    """Standard view orientations"""
    FRONT = ("*Front", 1)
    BACK = ("*Back", 2)
    LEFT = ("*Left", 3)
    RIGHT = ("*Right", 4)
    TOP = ("*Top", 5)
    BOTTOM = ("*Bottom", 6)
    ISOMETRIC = ("*Isometric", 7)
    TRIMETRIC = ("*Trimetric", 8)
    DIMETRIC = ("*Dimetric", 9)
    
    @classmethod
    def get(cls, name: str):
        """Get view tuple from name"""
        view_map = {
            "front": cls.FRONT,
            "back": cls.BACK,
            "left": cls.LEFT,
            "right": cls.RIGHT,
            "top": cls.TOP,
            "bottom": cls.BOTTOM,
            "isometric": cls.ISOMETRIC,
            "trimetric": cls.TRIMETRIC,
            "dimetric": cls.DIMETRIC,
        }
        return view_map.get(name.lower(), cls.ISOMETRIC)


# ============================================================================
# File Extensions
# ============================================================================

class SwFileTypes:
    """SolidWorks file extensions"""
    PART = ".sldprt"
    ASSEMBLY = ".sldasm"
    DRAWING = ".slddrw"
    PART_TEMPLATE = ".prtdot"
    ASSEMBLY_TEMPLATE = ".asmdot"
    DRAWING_TEMPLATE = ".drwdot"
    
    # Export formats
    STEP = ".step"
    STEP_ALT = ".stp"
    STL = ".stl"
    IGES = ".igs"
    PARASOLID = ".x_t"
    PARASOLID_BIN = ".x_b"
    DXF = ".dxf"
    DWG = ".dwg"
    PDF = ".pdf"
    
    @classmethod
    def is_valid_part(cls, ext: str) -> bool:
        return ext.lower() == cls.PART
    
    @classmethod
    def is_valid_export(cls, ext: str) -> bool:
        valid = [cls.STEP, cls.STEP_ALT, cls.STL, cls.IGES, cls.PARASOLID, cls.PARASOLID_BIN, cls.DXF, cls.DWG, cls.PDF]
        return ext.lower() in valid


# ============================================================================
# Selection Types (swSelectType_e from swconst.tlb)
# ============================================================================

class SwSelectType:
    """Entity selection types for SelectByID2 (matching swSelectType_e)"""
    FACE = "FACE"
    EDGE = "EDGE"
    VERTEX = "VERTEX"
    PLANE = "PLANE"
    AXIS = "AXIS"
    SKETCH = "SKETCH"
    SKETCHSEGMENT = "SKETCHSEGMENT"
    SKETCHPOINT = "SKETCHPOINT"
    BODYFEATURE = "BODYFEATURE"
    COMPONENT = "COMPONENT"
    EXTSKETCHSEGMENT = "EXTSKETCHSEGMENT"


# ============================================================================
# Default Values
# ============================================================================

class Defaults:
    """Default values for operations"""
    EXTRUDE_DEPTH_MM = 10.0
    FILLET_RADIUS_MM = 2.0
    CHAMFER_DISTANCE_MM = 2.0
    CIRCLE_RADIUS_MM = 25.0
    RECTANGLE_WIDTH_MM = 100.0
    RECTANGLE_HEIGHT_MM = 50.0
    
    CONNECTION_TIMEOUT = 120  # seconds
    RETRY_INTERVAL = 5        # seconds
    MAX_RETRIES = 3
