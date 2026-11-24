---
name: solidworks-diagnostics
description: >-
  Recovery and troubleshooting for the `solidworks` MCP server: blocking modal dialogs, rebuild errors,
  add-in loading, checkpoints, rollback and transactions, a hung COM worker, orphaned SolidWorks processes,
  and the consent-gated execute_code fallback. Use when a tool reports swModalDialogBlocking,
  swOperationTimeout or swWorkerBusy, when a rebuild fails or a feature shows an error, or for requests like
  "SolidWorks is stuck", "dismiss the dialog", "undo the last steps", "it froze", or "diagnosticar erros".
---

# SolidWorks diagnostics and recovery

> **First SolidWorks request of the session:** call `plugin_updates {"action": "status"}` once. If its state is `ask_user`, ask the user in one short question whether to turn on automatic updates for the SolidCADWorks plugin, then call it with `"enable"` or `"decline"` and carry on. For any other state, say nothing about updates.

Start with the least invasive step and escalate only when it fails: dialog → rebuild errors → rollback →
worker reset → process cleanup.

## 1. A dialog is blocking

`inspect_dialogs {}` lists every SolidWorks window with its text and button labels.
`dismiss_dialog {"action": "Yes"}` clicks a button by label; pass `hwnd` when there are several dialogs.
Both run outside the COM worker, so they work even while SolidWorks is blocked.

Read the dialog text before clicking. "Rebuild now?" is safe to accept. "Save changes?" or "Delete…?" are
the user's decision: ask them. Don't use `execute_code` to clear a dialog; it waits on the same blocked COM
thread and hangs.

## 2. A feature failed or the rebuild shows errors

`inspect_rebuild_errors {}` lists the failing features with their error codes and broken mates or
references. Fix the cause (a radius too large, an open sketch, a lost reference) with `set_dimension`,
`manage_feature` or by rebuilding the feature, then check again.

## 3. Undo a series of steps

- `transaction {"action": "checkpoint", "name": "before_ribs"}` records the end of the feature tree and saves
  a backup copy (`snapshot_path`).
- `transaction {"action": "rollback", "name": "before_ribs"}` deletes features added since. It does **not**
  revert dimension edits, suppression or renames of older features; restore those with `set_dimension` /
  `manage_feature`, or reopen the backup copy.
- `begin` / `commit` / `abort` bracket one multi-step edit (one transaction open at a time). `abort` removes
  features added since `begin`, undoes other geometry changes, and compares the volume. If it returns
  `success: false`, the model was not fully restored: inspect it with `get_feature_tree` and
  `get_dimensions` before continuing.

## 4. A tool timed out or the worker is stuck

`reset_sta_worker {}` abandons the hung COM worker thread (SolidWorks keeps running) and starts a fresh
one. The abandoned call may still finish later and change the model, so inspect the document before you
repeat any modifying tool. If SolidWorks itself is unresponsive or reports a MEMORY WARNING, save what you
can and use `restart_solidworks`.

## 5. Leftover SolidWorks processes

`supervisor_clean_processes {}` is a dry-run list of `SLDWORKS.exe` processes, with headless orphans marked.
Show it to the user. Only after they agree, call it with `"confirm": true` and the `target_pids` to end:
unsaved work in those processes is lost.

## 6. Add-ins

`load_addin {"name": "simulation"}` (also `toolbox`, `routing`, `motion`, `photoview`, or a DLL path) loads
an add-in into the running session and reports whether it is active. If Toolbox is not registered, use
`solidworks-din-parts` instead.

## 7. execute_code (consent-gated)

`execute_code` (`language`: `python`, `csharp`, `csharp_macro`) runs arbitrary code as the Windows user.
Use it only when no dedicated tool covers the operation.
- It refuses to run until the user agrees in chat (then pass `user_confirmed_risks: true`) or the server
  was started with `SW_MCP_ALLOW_EXEC=1`.
- Never set `user_confirmed_risks` on your own initiative.
- `"read_only": true` rolls Python back if the geometry changed, and makes C# refuse code that static
  analysis classifies as modifying.
- `reload_api` is a developer tool for hot-reloading the server; don't use it in normal work.

## Report tool problems

When a tool misbehaves (wrong result, misleading message, crash), call `report_issue` with the exact tool,
arguments, output, and expected vs actual. Then continue with a sound workaround, or stop if there is none.
