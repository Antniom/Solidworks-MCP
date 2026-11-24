---
name: solidworks-toolbox
description: >-
  SolidWorks Toolbox through the `solidworks` MCP server: loading the Toolbox add-in, choosing library
  hardware (ISO, DIN, ANSI bolts, nuts, washers, bearings), picking the size configuration, inserting it into
  assemblies and mating it to holes. Use only when the user asks for parts from their SolidWorks Toolbox,
  e.g. "insert a Toolbox bolt", "add standard washers from the Toolbox library", or "inserir parafuso da
  Toolbox". Otherwise standard parts are listed for download (solidworks-purchased-parts).
---

# SolidWorks Toolbox hardware

> **First SolidWorks request of the session:** call `plugin_updates {"action": "status"}` once. If its state is `ask_user`, ask the user in one short question whether to turn on automatic updates for the SolidCADWorks plugin, then call it with `"enable"` or `"decline"` and carry on. For any other state, say nothing about updates.

Toolbox parts are library files with one configuration per size: choose the size, insert the part, then
mate it like any other component.

Use this only when the user asked for Toolbox parts. By default, standard parts are listed for download
instead (`solidworks-purchased-parts`). No Toolbox licence and the user wants the part modeled? Use
`solidworks-din-parts`. For mates and checks in
general, see `solidworks-assemblies`.

## Workflow

1. `load_addin {"name": "toolbox"}`. If it reports that Toolbox is not registered, switch to
   `solidworks-purchased-parts`. The Toolbox data folder is set per installation (SolidWorks Options → Hole
   Wizard/Toolbox). Ask the user for it, or confirm the file exists, rather than guessing.
2. **Choose the size in the part, before inserting it.** `manage_configuration` only acts on the active
   document; no tool changes the configuration of a component already placed.
   1. `open_document` the Toolbox file.
   2. `manage_configuration {"action": "list"}`. Names vary between standards.
   3. `manage_configuration {"action": "activate", "name": "..."}`.
   4. Switch back to the assembly with `open_document`.
3. `add_component` with the Toolbox file, placed near its hole. Compare the box in the result with the size
   you expect (e.g. an M8x30 bolt is about 13 mm across the head and 35 mm long with the head). If it does
   not match, the wrong configuration was inserted.
4. Mate through the selection:
   - **Concentric:** the hole's cylinder and the bolt shank's cylinder.
   - **Coincident:** the seat face and the underside of the head. Pick the underside with
     `point_on_surface` about 1 mm outside the shank radius, at the head's height from the component box.
     A `normal` filter alone also matches the shank's end face.
   - Before each `add_mate`, check that `total_selection_count` is 2.
5. `check_interference` and explain every hit. Threads are not modelled, so a bolt in a tapped hole shows an
   expected overlap.

## Example: insert the bolt and centre it in an Ø8 hole of `Housing-1`

```json
load_addin {"name": "toolbox"}
open_document {"filepath": "C:/SOLIDWORKS Data/browser/ISO/Bolts and Screws/Hex Head/ISO 4017.sldprt"}
manage_configuration {"action": "list"}
manage_configuration {"action": "activate", "name": "ISO 4017 - M8 x 30-N"}
open_document {"filepath": "C:/Projects/Housing_Assembly.sldasm"}
add_component {"component_path": "C:/SOLIDWORKS Data/browser/ISO/Bolts and Screws/Hex Head/ISO 4017.sldprt", "x": 0, "y": 45, "z": 70, "unit": "mm"}
select {"by": "geometry", "target_type": "face", "surface_type": "cylinder", "radius": 4.0, "radius_tolerance": 0.2, "component": "Housing-1"}
select {"by": "geometry", "target_type": "face", "surface_type": "cylinder", "radius": 4.0, "radius_tolerance": 0.2, "component": "ISO 4017-1", "append": true}
add_mate {"mate_type": "concentric", "use_selection": true, "align": 0}
```

The paths and the configuration name are typical examples. Use the user's real Toolbox folder and the names
`list` returns.
