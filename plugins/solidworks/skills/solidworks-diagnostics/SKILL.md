---
name: solidworks-diagnostics
description: >-
  Dialog management, modal error recovery, dynamic add-in enablement, checkpoint rollbacks,
  and FeatureManager rebuild diagnostics in Dassault Systèmes SolidWorks via the MCP server (`solidworks`).
  Inspects out-of-band modal popups with `inspect_dialogs`, clicks resolution buttons (OK, Cancel, Yes, No,
  Rebuild, Close) with `dismiss_dialog`, dynamically loads add-ins (Simulation, Toolbox, Routing, Motion,
  PhotoView 360) via `load_addin`, rolls back state with checkpoints, diagnoses rebuild errors with
  `inspect_rebuild_errors`, and recovers from hung dispatcher states via `reset_sta_worker`.
  Triggers on prompts like "solidworks error", "modal popup", "dialog stuck", "missing relationships",
  "broken mate", "enable addin", "load simulation plugin", "load toolbox plugin", "dismiss dialog",
  "what's wrong dialog", "reset worker", "/solidworks-diagnostics".
---

# SolidWorks Diagnostics, Dialog Recovery & STA Architecture via MCP

## When this skill applies
Use this skill whenever SolidWorks encounters modal dialog blocks, rebuild errors, add-in loading failures, or automation hangs:
- Inspecting active Windows dialogs and modal error prompts (`inspect_dialogs`).
- Clicking dialog buttons programmatically (`dismiss_dialog`) with exact button targeting (`"OK"`, `"Cancel"`, `"Yes"`, `"No"`, `"Rebuild"`, `"Don't Rebuild"`, `"Close"`).
- Diagnosing FeatureManager rebuild warnings and broken mates (`inspect_rebuild_errors`).
- Programmatically loading and verifying SolidWorks add-in DLLs without restarting (`load_addin`).
- Creating and rolling back CAD model recovery checkpoints (`create_checkpoint`, `rollback_checkpoint`).
- Recovering safely from long-running or hung STA COM thread calls (`reset_sta_worker`).

For 3D part modeling, see `solidworks-modeling`.
For assembly mates and collisions, see `solidworks-assemblies`.
For FEA simulation analysis, see `solidworks-simulation`.
For drawing generation, see `solidworks-drawings`.

---

## Tool Selection Hierarchy: Native Tools First

> [!IMPORTANT]
> **STRICT TOOL EXECUTION HIERARCHY**:
> 1. **Priority 1 — Native MCP Tools (Always use first)**:
>    Always use dedicated SolidWorks MCP tools (`inspect_dialogs`, `dismiss_dialog`, `inspect_rebuild_errors`, `load_addin`, `create_checkpoint`, `rollback_checkpoint`, `reset_sta_worker`).
>    - `inspect_dialogs` and `dismiss_dialog` execute directly on the Win32 message pump thread outside the STA worker queue, allowing them to dismiss modal dialogs even when SolidWorks is blocked!
>    - `reset_sta_worker` re-initializes the STA worker thread and cleans up reentrant locks safely.
> 2. **Priority 2 — `execute_python` (Security-Gated Fallback ONLY)**:
>    Do **NOT** attempt to unblock dialogs using `execute_python` — if SolidWorks is blocked on a modal dialog, `execute_python` will hang waiting on COM!

---

## Complete Diagnostics Tool Inventory & Schemas

### 1. Modal Dialog Watchdog & Resolution
When a SolidWorks modal dialog appears (e.g. "Do you want to rebuild?", "File modified externally", "Feature failed to generate"):
- `inspect_dialogs {}`:
  - Scans all windows belonging to the attached `SLDWORKS.exe` process.
  - Returns `dialog_count`, window titles, class names, HWNDs, and available clickable button titles (e.g. `["&Yes", "&No", "Cancel"]`).
- `dismiss_dialog`:
  - `"action"`: Button text or action to perform (e.g. `"OK"`, `"Cancel"`, `"Yes"`, `"No"`, `"Rebuild"`, `"Close"`). Default: `"OK"`.
  - `"hwnd"`: Optional window handle (HWND) of the dialog. If omitted, targets the first active popup.
  - `"force_close"`: Boolean, sends `WM_CLOSE` directly to the window (default `false`).

### 2. Feature Rebuild Error Traversal (`inspect_rebuild_errors`)
- Traverses the entire FeatureManager tree and queries `Feature.GetErrorCode2`:
- Returns list of failing features with exact error codes:
  - Error code `0`: Feature built cleanly.
  - Non-zero codes: Specific topological or sketch errors (e.g. Fillet radius too large, disjoint extrusion, open sketch contour).
- Identifies broken assembly mates and missing reference geometry.

### 3. Dynamic Add-in Loading (`load_addin`)
Loads SolidWorks add-in DLLs into the running session without UI interaction:
- `"name"`: Friendly add-in name or DLL path:
  - `"simulation"` / `"cosmos"`: SolidWorks Simulation (`cosworks.dll`)
  - `"toolbox"` / `"browser"`: SolidWorks Toolbox (`swBrowser.dll`)
  - `"routing"`: SolidWorks Routing
  - `"motion"`: SolidWorks Motion (`motion.dll`)
- Returns `success: true` and confirms whether the COM add-in object is active.

### 4. Checkpoints & State Rollback
- `create_checkpoint {"name": str, "description": str}`: Takes an in-memory snapshot of current document state.
- `rollback_checkpoint {"name": str}`: Reverts document state to previous snapshot if subsequent operations fail.

### 5. STA Dispatcher Recovery (`reset_sta_worker`)
If an operation times out or a COM call leaves the dispatcher thread unresponsive:
- `reset_sta_worker {}`:
  - Gracefully terminates the hung STA worker thread without killing the `SLDWORKS.exe` process.
  - Re-initializes a fresh STA apartment thread, re-acquires the COM application pointer, and clears lingering `RLock` reentrant acquisitions.

---

## Standard Dialog Recovery Recipe

```json
// 1. Detect if a modal dialog is blocking automation
inspect_dialogs {}

// Sample response:
// {
//   "dialog_count": 1,
//   "dialogs": [
//     {
//       "hwnd": 131248,
//       "title": "SOLIDWORKS",
//       "text": "The model has been modified. Do you want to rebuild now?",
//       "buttons": ["&Yes", "&No", "Cancel"]
//     }
//   ]
// }

// 2. Dismiss dialog by clicking affirmative resolution button
dismiss_dialog {
  "action": "Yes"
}

// 3. Inspect FeatureManager tree for any lingering rebuild errors
inspect_rebuild_errors {}
```
