---
name: solidworks-interactive
description: >-
  Interactive studio for the active SolidWorks model: dimension sliders and a live viewport, an MCP App
  opened with `show_interactive_studio`. Use when the user wants to tweak or explore dimensions
  of an existing part or assembly visually, or try sizes after modelling, e.g. "show sliders", "let me adjust
  the size", "interactive view", or "estúdio interativo", "ajustar dimensões com sliders". Also use when a
  message starts "I have adjusted the SolidWorks model parameters via the interactive studio".
---

# SolidWorks Interactive Studio

> **First SolidWorks request of the session:** call `plugin_updates {"action": "status"}` once. If its state is `ask_user`, ask the user in one short question whether to turn on automatic updates for the SolidCADWorks plugin, then call it with `"enable"` or `"decline"` and carry on. For any other state, say nothing about updates.

`show_interactive_studio` turns the active model's driving dimensions into sliders next to a viewport image.
Moving a slider rebuilds the model in SolidWorks and refreshes the image.

The studio is an [MCP App](https://github.com/modelcontextprotocol/ext-apps) (`ui://solidworks/studio.html`),
so any client that supports MCP Apps (Claude, Codex, Antigravity, ...) shows it inline next to the tool result.

For a single known change ("make it 60 mm long"), call `set_dimension` directly instead.

## Opening the studio

Rename features first (`manage_feature` with `"action": "rename"`) and pass labels, so sliders read
"Head Thickness" rather than "D1":

```json
{
  "view_name": "*Isometric",
  "feature_labels": {"Boss-Extrude1": "Hex Bolt Head", "Boss-Extrude2": "Bolt Shaft"},
  "dimension_labels": {
    "D1@Boss-Extrude1@Part1.Part": "Head Thickness",
    "D1@Boss-Extrude2@Part1.Part": "Shaft Length"
  }
}
```

`feature_filter` (for example `"Boss-Extrude1"`) limits the studio to one feature. Dimension names come
from `get_dimensions`.

## Showing it to the user

- Where the client supports MCP Apps, the studio appears inline as soon as the tool returns. Nothing
  more is needed.
- If it does not appear (for example in a terminal client), there is no browser fallback: list the
  dimensions from the result and make the changes the user asks for with `set_dimension`.

Do not paste the raw JSON into the chat, and never call `studio_action` yourself: it belongs to the studio
view. Say what the sliders control and let the user drive.

## What the user can do

- Drag a slider: the dimension changes in SolidWorks and the viewport image updates. If the rebuild fails,
  the slider returns to its previous value and the status line says why.
- Type an exact value in the number field next to a slider (clamped to the slider's range).
- Pick a standard view (Isometric, Front, Top, Right, Back, Left, Bottom); the image refreshes.
- **Reset** restores every dimension to its value when the studio was opened.
- **Prompt Agent** sends (or copies to the clipboard) a summary of what changed.

Sliders step by 0.5 to 5 mm depending on the size of the dimension (1 degree for angles). For a value
outside the slider's range, use `set_dimension`.

## After a Prompt Agent message

The message looks like:

> I have adjusted the SolidWorks model parameters via the interactive studio:
> - Head Thickness (D1@Boss-Extrude1@Part1.Part) on Hex Bolt Head: changed from 4.8 to 6 mm
> Please verify the model geometry and update any associated technical drawings or stress simulations.

Then:
1. `verify_model` and `inspect_rebuild_errors` to confirm the geometry is sound.
2. Update a drawing (`create_drawing_from_model`, `export_drawing`) if one was requested earlier.
3. Re-run FEA with `solidworks-simulation` if a study exists.
4. Report the new bounding box, volume and the dimensions that changed.

## Notes

- The studio shows the model as it was when the tool ran. After adding or removing features, call
  `show_interactive_studio` again to rebuild the slider list.
- Driven (reference) dimensions are left out because they cannot be set.
