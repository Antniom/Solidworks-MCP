---
name: solidworks-welcome-demo
description: >-
  First-run check and welcome demo for the `solidworks` MCP server: confirms the tools respond, then models
  an extruded red "SOLIDWORKS AI" plaque and shows it. Use right after the SolidWorks MCP or SolidCADWorks
  plugin has been installed or the client restarted after setup, or when the user asks for "the welcome
  demo", "a demo plaque", "test the SolidWorks connection", "show me it works", or "demonstração".
---

# SolidWorks welcome demo

The demo shows that the server reaches SolidWorks and can model, colour and render a part. It takes under
a minute.

## 1. Tell the user what will happen

Say in one or two sentences that you will run a few read-only checks, then model a small demo part. Each
tool may ask for permission the first time. Mention that choosing "Always allow" for these read-only tools
avoids repeated prompts later. It is their choice: don't press them, and continue whatever they pick.

## 2. Read-only checks

```json
get_capabilities {}
list_open_documents {}
inspect_dialogs {}
plugin_updates {"action": "status"}
```

If `plugin_updates` returns state `ask_user`, ask the user whether to turn on automatic updates for the
SolidCADWorks plugin: new versions then install by themselves at the start of a later session. On yes call
`plugin_updates {"action": "enable"}`; on no, `plugin_updates {"action": "decline"}`. Either way they won't be
asked again.

If `get_capabilities` reports that SolidWorks is not installed or cannot be reached, stop and tell the
user. If `inspect_dialogs` finds a blocking dialog, follow `solidworks-diagnostics` first.

## 3. Demo plaque

```json
connect_solidworks {}
create_document {"type": "part"}
set_units {"unit": "mm"}
create_sketch {"plane": "Front"}
draw_text {"text": "SOLIDWORKS AI", "x": -140, "y": -12.5, "height": 25, "font_name": "Arial", "bold": true, "unit": "mm"}
extrude_sketch {"depth": 10, "unit": "mm"}
manage_feature {"action": "set_color", "feature_name": "Boss-Extrude1", "color_rgb": [235, 20, 20]}
capture_viewport {"view_name": "*Isometric"}
```

Look at the capture before you report. Tell the user what was built (the box from the extrude result),
then suggest a next step, such as modeling one of their own parts.
