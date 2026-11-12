---
name: solidworks-interactive
description: >-
  Interactive studio for the active SolidWorks model: dimension sliders and a live viewport, described in
  A2UI and opened with `show_interactive_studio`. Use when the user wants to tweak or explore dimensions
  visually, e.g. "show sliders", "let me adjust the size", "interactive view", or "estúdio interativo",
  "ajustar dimensões com sliders".
---

# SolidWorks Interactive Studio

`show_interactive_studio` turns the active model's driving dimensions into sliders next to a viewport image.
Moving a slider rebuilds the model in SolidWorks and refreshes the image.

The studio is described with [A2UI](https://github.com/a2ui-project/a2ui) v0.9 messages (basic catalog) and
drawn by the stock A2UI Lit renderer, so the same payload works in any A2UI-capable client.

## When to use it

- The user wants to adjust, tune or explore dimensions of an existing part or assembly.
- The user asks for sliders, visual controls or an interactive view.
- A part has just been modeled and the user wants to try different sizes.
- A message produced by the studio's **Prompt Agent** button arrives (see below).

For a single known change ("make it 60 mm long") call `set_dimension` directly instead.

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

- **MCP Apps clients** (Claude Desktop, VS Code and others) display the studio inline automatically.
- **Everywhere else**, give the user the `studioUrl` from the result. It is a `http://127.0.0.1:...` link
  that works only on their machine and carries a one-time access token, so pass it on exactly as returned.
- An A2UI-capable client can render `data.a2ui.messages` itself.

Do not paste the raw JSON into the chat. Say what the sliders control and let the user drive.

## What the user can do

- Drag a slider: the dimension changes in SolidWorks and the viewport image updates. If the rebuild fails,
  the slider returns to its previous value and the status line says why.
- Pick a standard view (Isometric, Front, Top, Right, Back, Left, Bottom) and refresh the image.
- **Reset** restores every dimension to its value when the studio was opened.
- **Prompt Agent** sends (or copies to the clipboard) a summary of what changed.

Sliders move in whole units (1 mm or 1 degree). For a finer value, use `set_dimension`.

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
