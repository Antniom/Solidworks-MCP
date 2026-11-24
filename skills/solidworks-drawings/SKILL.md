---
name: solidworks-drawings
description: >-
  2D engineering drawings in SolidWorks through the `solidworks` MCP server: drawing sheets, orthographic,
  isometric and section views, imported model dimensions, BOM tables, and PDF/DXF/DWG/PNG export. Use for any
  request to document a part or assembly on paper or for a workshop, e.g. "create drawing", "make a
  manufacturing drawing", "section view", "insert BOM", "export to PDF/DXF for the laser cutter", or "criar
  desenho", "vista de corte", "lista de materiais".
---

# SolidWorks drawings

> **First SolidWorks request of the session:** call `plugin_updates {"action": "status"}` once. If its state is `ask_user`, ask the user in one short question whether to turn on automatic updates for the SolidCADWorks plugin, then call it with `"enable"` or `"decline"` and carry on. For any other state, say nothing about updates.

The tool schemas list every argument. This file covers the gotchas, layout, and the order of operations.

Related skills: models → `solidworks-modeling` / `solidworks-assemblies`; dialogs and stuck sessions →
`solidworks-diagnostics`. `execute_code` is consent-gated. Use it only for drawing operations no tool
provides.

## Units differ between tools

| Tool | Sheet coordinates in |
| :--- | :--- |
| `create_drawing_from_model` `x`, `y` | **metres** (A3 is 0.420 × 0.297) |
| `create_section_view`, `add_drawing_dimension`, `insert_bom_table` `x`, `y` | the call's `unit`, default mm |

Passing millimetres to `create_drawing_from_model` puts the view far off the sheet.

## Workflow

1. **Save the model first.** `create_drawing_from_model` needs a file on disk; it opens the model and links
   the view to it.
2. `create_drawing_from_model` creates the sheet (`paper_size`) and the first view. Pick `view_name` from
   `*Front`, `*Top`, `*Right`, `*Isometric`.
3. **Layout (third-angle, ISO/ASME):**
   - Front view lower left.
   - Top view directly above it (same x).
   - Right view directly to its right (same y).
   - Isometric upper right.
   - Keep everything clear of the title block in the lower right (roughly x > 180 mm, y < 60 mm on A4).
4. `create_section_view` through the symmetry plane or the cavity you need to show. It requires `x`, `y`
   for the new view. `parent_view` takes a name such as `"Drawing View1"` (from the
   `create_drawing_from_model` result), and `line_x1…line_y2` give the cut line.
5. `import_model_items {"all_views": true}` brings in the model's driving dimensions. Do this before adding
   any manual dimensions with `add_drawing_dimension`, which is only for reference or inspection values.
6. Assemblies: `insert_bom_table`, anchored top-right or bottom-right, without covering views.
   - `parts_only` flattens subassemblies (useful for purchasing).
   - `indented` keeps the hierarchy.
   - The BOM template is found automatically; pass `template_path` only for a custom one.
7. `export_drawing`:
   - `.pdf` for review;
   - `.dxf` / `.dwg` for laser, waterjet or CAM;
   - `.png` / `.jpg` for reports.
8. Capture or export to PNG and look at the result: overlapping views and dimensions crossing geometry are
   only visible in the image.

## Example: A3 drawing of a saved assembly

```json
save_document {"filepath": "C:/Projects/Gearbox_Assembly.sldasm"}
create_drawing_from_model {"model_path": "C:/Projects/Gearbox_Assembly.sldasm", "view_name": "*Front", "x": 0.15, "y": 0.135, "paper_size": "A3"}
create_section_view {"x": 280.0, "y": 135.0, "section_label": "A", "parent_view": "Drawing View1", "line_x1": 150.0, "line_y1": 50.0, "line_x2": 150.0, "line_y2": 220.0}
import_model_items {"all_views": true}
insert_bom_table {"x": 380.0, "y": 280.0, "bom_type": "parts_only", "anchor_type": 2}
export_drawing {"filepath": "C:/Projects/Gearbox_Assembly_Drawing.pdf"}
export_drawing {"filepath": "C:/Projects/Gearbox_Assembly_Drawing.dxf"}
```
