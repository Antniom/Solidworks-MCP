---
name: solidworks-drawings
description: >-
  2D engineering drafting, sheet configuration, orthographic projections, isometric views,
  section views, model dimension imports, and Bill of Materials (BOM) tables in Dassault Systèmes
  SolidWorks via the MCP server (`solidworks`).
  Manages paper sizes (A4, A3, A2, A1, Letter), multi-view orthographic placement, creating section
  views with `create_section_view`, importing model feature dimensions with `import_model_items`,
  adding drawing dimensions with `add_drawing_dimension`, inserting BOM tables with `insert_bom_table`,
  and exporting to vector PDF, DXF, DWG, and raster images. Triggers on English prompts like "create drawing",
  "technical drawing", "drawing sheet", "orthographic view", "isometric view", "section view",
  "insert bom table", "drawing dimensions", "export drawing to pdf", "dxf export", "/solidworks-drawings"
  as well as Portuguese (pt-PT) phrases like "criar desenho", "desenho técnico", "folha de desenho",
  "vista ortográfica", "vista isométrica", "vista de corte", "tabela bom", "dimensões no desenho",
  "exportar desenho para pdf", "exportar dxf".
---

# SolidWorks 2D Engineering Drawings, Detailing & BOM Tables via MCP

## When this skill applies
Use this skill for generating production-ready 2D manufacturing drawings (`.slddrw`) from 3D parts or assemblies:
- Creating drawing documents and sheets in standard sizes (A4, A3, A2, A1, Letter).
- Placing standard orthographic views (Front, Top, Right) and 3D Isometric views (`create_drawing_from_model`).
- Creating full or offset section cut views with custom letters/labels (`create_section_view`).
- Automatically importing dimensions and annotations from 3D model features (`import_model_items`).
- Placing manual driven dimensions between geometry points (`add_drawing_dimension`).
- Inserting structured Bill of Materials (BOM) tables on assembly drawings (`insert_bom_table`).
- Exporting drawings to vector PDF, CNC DXF/DWG, or high-res images (`export_drawing`).

For 3D part modeling, see `solidworks-modeling`.
For assembly modeling, see `solidworks-assemblies`.
For FEA simulation plots, see `solidworks-simulation`.
For dialog and rebuild recovery, see `solidworks-diagnostics`.

---

## Tool Selection Hierarchy: Native Tools First

> [!IMPORTANT]
> **STRICT TOOL EXECUTION HIERARCHY**:
> 1. **Priority 1 — Native MCP Tools (Always use first)**:
>    Always use dedicated SolidWorks MCP tools (`create_new_drawing`, `create_drawing_from_model`, `create_section_view`, `import_model_items`, `add_drawing_dimension`, `insert_bom_table`, `export_drawing`, `capture_viewport`).
>    - All tools execute safely on the dedicated STA dispatcher thread.
>    - `create_drawing_from_model` automatically maps model references and computes safe layout scales.
>    - `insert_bom_table` dynamically resolves `.sldbomtbt` templates without hardcoded paths.
>    - `create_section_view` handles section line selection and view generation robustly.
> 2. **Priority 2 — `execute_python` (Security-Gated Fallback ONLY)**:
>    Do **NOT** use `execute_python` for routine drawing creation, view placement, or exports.
>    `execute_python` is locked by default and requires explicit user consent in chat.

---

## Complete Drawing Tool Inventory & Schemas

### 1. Document & View Setup
- `create_new_drawing {"paper_size": "A4" | "A3" | "A2" | "A1" | "Letter"}`: Creates an empty drawing sheet.
- `create_drawing_from_model`:
  - `"model_path"`: Full path to `.sldprt` or `.sldasm` on disk (must be saved).
  - `"view_name"`: Target view to insert (default: `"*Isometric"`, or `"*Front"`, `"*Top"`, `"*Right"`).
  - `"x"`, `"y"`: Placement coordinates on sheet (in mm).
  - `"paper_size"`: `"A4"` (default) or `"A3"`

### 2. Section Views (`create_section_view`)
Generates an aligned or offset section cut view from a sketch line in a parent drawing view:
- `"x"`, `"y"`: **Required** sheet placement coordinates for the resulting section view.
- `"section_label"`: Single letter identifier (e.g. `"A"`, `"B"`, `"C"`, default: `"A"`).
- `"parent_view"`: Name of parent drawing view containing the section line (e.g. `"Drawing View1"`).
- `"line_x1"`, `"line_y1"`, `"line_x2"`, `"line_y2"`: Optional coordinates defining the section cut line.
- `"options"`: Bitmask options (default `32`: `swCreateSectionView_NotAligned`).
- `"unit"`: Coordinate unit (`"mm"`, default: `"mm"`).

### 3. Dimensions & Detailing
- `import_model_items`:
  - Automatically imports feature dimensions, tolerances, and annotations from the 3D model into drawing views:
  - `"option"`: Bitflag for import options (default: `1`).
  - `"types"`: Bitflag for item types to import (default: `1`).
  - `"all_views"`: Boolean, import into all drawing views (default: `true`).
- `add_drawing_dimension`:
  - Places a dimension at the specified sheet coordinates:
  - `"x"`, `"y"`: **Required** dimension placement coordinates on the sheet.
  - `"z"`: Optional Z coordinate (default: `0.0`).
  - `"unit"`: `"mm"` (default: `"mm"`).

### 4. Bill of Materials (BOM) Tables (`insert_bom_table`)
Inserts an industry-standard BOM table into the active drawing sheet:
- `"template_path"`: Path to `.sldbomtbt` template file (auto-detected dynamically from SolidWorks install if omitted).
- `"x"`, `"y"`: Table insertion anchor coordinates on sheet.
- `"bom_type"`:
  - `"top_level"`: Summarizes top-level assemblies and components.
  - `"parts_only"`: Flattens all subassemblies into raw individual parts (ideal for procurement).
  - `"indented"`: Hierarchical multi-level indented bill of materials.
- `"anchor_type"`: `1` = Top-Left, `2` = Top-Right, `3` = Bottom-Left, `4` = Bottom-Right.
- `"configuration"`: Configuration name or empty string for active configuration.

### 5. Vector & Image Export (`export_drawing`)
- `"filepath"`: Destination output path.
- Supported extensions:
  - `.pdf`: Vector PDF for technical review and printing.
  - `.dxf`, `.dwg`: 2D CAD formats for CNC laser/waterjet cutting and machining.
  - `.png`, `.jpg`: High-resolution raster images for reports and presentations.

---

## The 5 Rules for SolidWorks Drawings

1. **3D Model Must Be Saved to Disk First**:
   - `create_drawing_from_model` requires a persistent file path on disk to establish the external document link. Never call it on an unsaved buffer.
2. **Isometric View Uses ID Constant 7**:
   - In SolidWorks COM, view ID 1 is Front View, ID 4 is Right View, ID 5 is Top View, and **ID 7 is Isometric**. Passing ID 1 when requesting Isometric erroneously returns Front view.
3. **Always Position Views Outside the Title Block**:
   - Standard A4/A3 drawing templates place the title block in the bottom-right corner ($x > 180\text{ mm}, y < 60\text{ mm}$ on A4). Place orthographic views in the upper-left quadrant to avoid overlapping border text.
4. **Import Model Items Before Adding Manual Dimensions**:
   - Call `import_model_items` first to bring in true driving model dimensions. Only use `add_drawing_dimension` for secondary reference dimensions or inspection callouts.
5. **Dynamic BOM Template Resolution**:
   - Never hardcode `C:\Program Files\SOLIDWORKS Corp\...` in scripts. The MCP automatically locates `bom-standard.sldbomtbt` across registry and install directories.

---

## Standard Drawing Production Recipe

```json
// 1. Ensure Model is Saved
connect_solidworks {}
save_document {"filepath": "C:/Projects/Gearbox_Assembly.sldasm"}

// 2. Generate Drawing Sheet with Initial View
create_drawing_from_model {
  "model_path": "C:/Projects/Gearbox_Assembly.sldasm",
  "view_name": "*Front",
  "x": 150.0,
  "y": 135.0,
  "paper_size": "A3"
}

// 3. Create Full Section Cut View A-A Placed at (x: 280, y: 135)
create_section_view {
  "x": 280.0,
  "y": 135.0,
  "section_label": "A",
  "parent_view": "Drawing View1",
  "line_x1": 150.0,
  "line_y1": 50.0,
  "line_x2": 150.0,
  "line_y2": 220.0
}

// 4. Import 3D Model Driving Dimensions into Views
import_model_items {
  "all_views": true
}

// 5. Insert Parts-Only Bill of Materials Table in Top-Right Corner
insert_bom_table {
  "x": 380.0,
  "y": 280.0,
  "bom_type": "parts_only",
  "anchor_type": 2
}

// 6. Export Vector PDF & DXF for Fabrication
export_drawing {"filepath": "C:/Projects/Gearbox_Assembly_Drawing.pdf"}
export_drawing {"filepath": "C:/Projects/Gearbox_Assembly_Drawing.dxf"}
```
