---
name: solidworks-diagnostics
description: >-
  Recovery and troubleshooting for the `solidworks` MCP server: blocking modal dialogs, rebuild errors,
  add-in loading, checkpoints and transactions, a hung COM worker, and orphaned SolidWorks processes. Use
  when a tool reports swModalDialogBlocking, swOperationTimeout or swWorkerBusy, when a rebuild fails, or
  for requests like "SolidWorks is stuck", "dismiss the dialog", "undo the last steps", "diagnosticar erros".
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
- `create_checkpoint {"name": str, "description": str}`: Records the current end of the feature tree and saves a backup copy of the document (path returned as `snapshot_path`).
- `rollback_checkpoint {"name": str}`: Deletes every feature added after the checkpoint. It does **not** revert dimension edits, suppression or renames made to older features; restore those with `set_dimension` / `manage_feature`, or reopen the backup copy.
- `transaction {"action": "begin" | "commit" | "abort", "name": str}`: Brackets a multi-step edit. `abort` removes features added since `begin` and undoes other geometry changes, then compares the model volume with its value at `begin`. If it returns `success: false`, the model was **not** fully restored: inspect it with `list_features` and `get_dimensions` before continuing. Only one transaction can be open at a time.

### 5. STA Dispatcher Recovery (`reset_sta_worker`)
If an operation times out or a COM call leaves the dispatcher thread unresponsive:
- `reset_sta_worker {}`:
  - Abandons the hung worker thread (SolidWorks keeps running) and starts a fresh STA thread; the next tool call reconnects.
  - The abandoned call may still finish later and change the model, so inspect the document before repeating a mutating tool.

### 6. Orphaned Processes (`supervisor_clean_processes`)
- `supervisor_clean_processes {}`: Dry-run audit of every `SLDWORKS.exe` process; lists headless orphans left behind by crashed sessions.
- `supervisor_clean_processes {"confirm": true, "target_pids": [int]}`: Terminates the listed orphans. Unsaved work in those processes is lost, so show the user the audit and get their agreement first.

### 7. Code-Execution Tools (consent-gated)
`execute_python`, `sw_execute_csharp` and `sw_execute_macro` run arbitrary code as the Windows user and share one gate: they refuse to run until the user has agreed in chat (then pass `user_confirmed_risks: true`) or the server was started with `SW_MCP_ALLOW_EXEC=1`. Never set `user_confirmed_risks` on your own initiative. `reload_api {}` is a developer tool that hot-reloads the server's Python modules; do not use it in normal work.

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
