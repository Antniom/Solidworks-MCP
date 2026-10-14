"""
Regression tests verifying solidworks_mcp.constants against swconst.tlb
----------------------------------------------------------------------
Asserts that all enum values defined in solidworks_mcp.constants
strictly match the official SolidWorks 2025 type library.
"""

from pathlib import Path
import pytest
import pythoncom
from solidworks_mcp import constants


def get_tlb_enums():
    tlb_path = Path(r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\swconst.tlb")
    if not tlb_path.exists():
        pytest.skip(f"swconst.tlb not found at {tlb_path}")
    tlb = pythoncom.LoadTypeLib(str(tlb_path))
    enums = {}
    for i in range(tlb.GetTypeInfoCount()):
        tinfo = tlb.GetTypeInfo(i)
        attr = tinfo.GetTypeAttr()
        name = tlb.GetDocumentation(i)[0]
        if attr.typekind == pythoncom.TKIND_ENUM:
            vals = {}
            for v in range(attr.cVars):
                vardesc = tinfo.GetVarDesc(v)
                varname = tinfo.GetDocumentation(vardesc.memid)[0]
                vals[varname] = vardesc.value
            enums[name] = vals
    return enums


def test_sw_end_conditions():
    """Verify SwEndConditions values against swEndConditions_e in swconst.tlb."""
    tlb_enums = get_tlb_enums()
    assert "swEndConditions_e" in tlb_enums
    expected = tlb_enums["swEndConditions_e"]

    assert constants.SwEndConditions.swEndCondBlind == expected["swEndCondBlind"] == 0
    assert constants.SwEndConditions.swEndCondThroughAll == expected["swEndCondThroughAll"] == 1
    assert constants.SwEndConditions.swEndCondThroughNext == expected["swEndCondThroughNext"] == 2
    assert constants.SwEndConditions.swEndCondUpToVertex == expected["swEndCondUpToVertex"] == 3
    assert constants.SwEndConditions.swEndCondUpToSurface == expected["swEndCondUpToSurface"] == 4
    assert constants.SwEndConditions.swEndCondOffsetFromSurface == expected["swEndCondOffsetFromSurface"] == 5
    assert constants.SwEndConditions.swEndCondMidPlane == expected["swEndCondMidPlane"] == 6
    assert constants.SwEndConditions.swEndCondUpToBody == expected["swEndCondUpToBody"] == 7
    assert constants.SwEndConditions.swEndCondThroughAllBoth == expected["swEndCondThroughAllBoth"] == 9
    assert constants.SwEndConditions.swEndCondUpToSelection == expected["swEndCondUpToSelection"] == 10
    assert constants.SwEndConditions.swEndCondUpToNext == expected["swEndCondUpToNext"] == 11


def test_sw_mate_types():
    """Verify SwMateTypes values against swMateType_e in swconst.tlb."""
    tlb_enums = get_tlb_enums()
    assert "swMateType_e" in tlb_enums
    expected = tlb_enums["swMateType_e"]

    assert constants.SwMateTypes.swMateCOINCIDENT == expected["swMateCOINCIDENT"] == 0
    assert constants.SwMateTypes.swMateCONCENTRIC == expected["swMateCONCENTRIC"] == 1
    assert constants.SwMateTypes.swMatePERPENDICULAR == expected["swMatePERPENDICULAR"] == 2
    assert constants.SwMateTypes.swMatePARALLEL == expected["swMatePARALLEL"] == 3
    assert constants.SwMateTypes.swMateTANGENT == expected["swMateTANGENT"] == 4
    assert constants.SwMateTypes.swMateDISTANCE == expected["swMateDISTANCE"] == 5
    assert constants.SwMateTypes.swMateANGLE == expected["swMateANGLE"] == 6
    assert constants.SwMateTypes.swMateWIDTH == expected["swMateWIDTH"] == 11  # Crucial fix from 18 -> 11
    assert constants.SwMateTypes.swMateLOCK == expected["swMateLOCK"] == 16
    assert constants.SwMateTypes.swMateLINEARCOUPLER == expected["swMateLINEARCOUPLER"] == 18


def test_sw_document_types():
    """Verify SwDocumentTypes values against swDocumentTypes_e in swconst.tlb."""
    tlb_enums = get_tlb_enums()
    assert "swDocumentTypes_e" in tlb_enums
    expected = tlb_enums["swDocumentTypes_e"]

    assert constants.SwDocumentTypes.swDocNONE == expected["swDocNONE"] == 0
    assert constants.SwDocumentTypes.swDocPART == expected["swDocPART"] == 1
    assert constants.SwDocumentTypes.swDocASSEMBLY == expected["swDocASSEMBLY"] == 2
    assert constants.SwDocumentTypes.swDocDRAWING == expected["swDocDRAWING"] == 3


def test_sw_mate_align():
    """Verify SwMateAlign values against swMateAlign_e in swconst.tlb."""
    tlb_enums = get_tlb_enums()
    assert "swMateAlign_e" in tlb_enums
    expected = tlb_enums["swMateAlign_e"]

    assert constants.SwMateAlign.swMateAlignALIGNED == expected["swMateAlignALIGNED"] == 0
    assert constants.SwMateAlign.swMateAlignANTI_ALIGNED == expected["swMateAlignANTI_ALIGNED"] == 1
    assert constants.SwMateAlign.swMateAlignCLOSEST == expected["swMateAlignCLOSEST"] == 2


def test_sw_dimension_driven_state():
    """Verify SwDimensionDrivenState values against swDimensionDrivenState_e in swconst.tlb."""
    tlb_enums = get_tlb_enums()
    assert "swDimensionDrivenState_e" in tlb_enums
    expected = tlb_enums["swDimensionDrivenState_e"]

    assert constants.SwDimensionDrivenState.swDimensionDrivenUnknown == expected["swDimensionDrivenUnknown"] == 0
    assert constants.SwDimensionDrivenState.swDimensionDriven == expected["swDimensionDriven"] == 1
    assert constants.SwDimensionDrivenState.swDimensionDriving == expected["swDimensionDriving"] == 2


def test_sw_errors_canonical_uniqueness():
    """Verify SwErrors unique semantics (no duplicate meaning codes, split timeout/busy)."""
    assert constants.SwErrors.swOperationTimeout == 116
    assert constants.SwErrors.swWorkerBusy == 118
    assert constants.SwErrors.swInvalidCall == 9
    assert constants.SwErrors.swNoActiveDocument == 101
    assert constants.SwErrors.swInvalidInput == 107
    assert constants.SwErrors.swInvalidParameter == 107
    assert constants.SwErrors.swInvalidParameters == 107
    assert constants.SwErrors.swSelectionError == 104
    assert constants.SwErrors.swSelectionFailed == 104
