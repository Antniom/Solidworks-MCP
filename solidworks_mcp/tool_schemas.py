"""
SolidWorks MCP tool catalog
---------------------------
Input schemas for every tool the server exposes. Handlers live in server.py.
"""

from functools import lru_cache
from typing import Any, Dict, List, Optional

from jsonschema import Draft202012Validator
from mcp.types import Tool

# Tools whose "unit" argument is not a length unit.
# (a pressure/force unit, the unit being set, or a mate value that may be an angle)
_NON_LENGTH_UNIT_TOOLS = {"add_study_load", "set_units", "drive_kinematic_motion"}
_LENGTH_UNIT_HINT = "Length unit (mm, inch, m, cm). Defaults to the unit chosen with set_units (mm initially)."


def _build_tools() -> List[Tool]:
    tools = [
        # Connection Tools
        Tool(
            name="connect_solidworks",
            description="Connect to SolidWorks. Launches if not running. Accepts optional PID to target a specific instance.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pid": {"type": "integer", "description": "Optional process ID (PID) of SolidWorks instance to connect to"}
                },
                "required": []
            }
        ),
        Tool(
            name="get_capabilities",
            description="SolidWorks installation details (path, version, templates), system capabilities, add-in status and STA runtime diagnostics. Works before connecting.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        
        # Document Tools
        Tool(
            name="create_document",
            description="Create a new part, assembly or drawing document.",
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["part", "assembly", "drawing"], "default": "part", "description": "Document type"},
                    "paper_size": {"type": "string", "enum": ["A4", "A3", "A2", "A1", "Letter"], "default": "A4", "description": "Sheet size (drawings only)"}
                },
                "required": []
            }
        ),
        Tool(
            name="open_document",
            description="Open an existing SolidWorks document.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Path to file"}
                },
                "required": ["filepath"]
            }
        ),
        Tool(
            name="save_document",
            description="Save the active document.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Path to save (optional for Save As)"}
                },
                "required": []
            }
        ),
        Tool(
            name="close_document",
            description="Close the active document.",
            inputSchema={
                "type": "object",
                "properties": {
                    "save": {"type": "boolean", "default": False, "description": "Save before closing"}
                },
                "required": []
            }
        ),
        Tool(
            name="get_document_info",
            description="Get information about the active document.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="list_open_documents",
            description="List all open documents.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="manage_configuration",
            description="Manage document configurations (list, add, activate, rename, delete) in part or assembly documents.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "add", "activate", "rename", "delete"],
                        "description": "Configuration management action"
                    },
                    "name": {"type": "string", "description": "Configuration name to add, activate, rename, or delete"},
                    "new_name": {"type": "string", "description": "New configuration name for 'rename' action"},
                    "description": {"type": "string", "description": "Optional description for 'add' action"},
                    "comment": {"type": "string", "description": "Optional comment for 'add' action"}
                },
                "required": ["action"]
            }
        ),
        Tool(
            name="manage_custom_properties",
            description="Manage document custom properties (list, get, set, delete) at file-level or configuration-specific level.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "set", "delete"],
                        "description": "Custom property action"
                    },
                    "name": {"type": "string", "description": "Custom property name"},
                    "value": {"description": "Property value (for 'set' action)"},
                    "property_type": {
                        "type": "string",
                        "enum": ["text", "number", "date", "yesno"],
                        "default": "text",
                        "description": "Data type for 'set' action"
                    },
                    "configuration": {
                        "type": "string",
                        "default": "",
                        "description": "Configuration name for configuration-specific property, or empty string for file-level"
                    }
                },
                "required": ["action"]
            }
        ),
        
        # Sketch Tools
        Tool(
            name="create_sketch",
            description=(
                "Create a new sketch on a reference plane (optionally offset), on a body face by 1-based face_index, "
                "or on the body face found at face_point. Sketch on faces rather than planes for cuts into existing geometry."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "plane": {
                        "type": "string",
                        "enum": ["Front", "Top", "Right", "XY", "XZ", "YZ"],
                        "default": "Front",
                        "description": "Plane to sketch on (Front/XY, Top/XZ, Right/YZ)"
                    },
                    "offset": {
                        "type": "number",
                        "default": 0.0,
                        "description": "Optional offset distance from plane (creates an offset RefPlane automatically)"
                    },
                    "face_index": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-based face index on body 1 to sketch on instead of a reference plane (see list_topology)"
                    },
                    "face_point": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "Point [x, y, z] lying on an existing body face to sketch on instead of a reference plane"},
                    "unit": {
                        "type": "string",
                        "enum": ["mm", "inch", "m", "cm"],
                        "description": "Unit for offset distance and face_point"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="draw_line",
            description="Draw a line in the active sketch.",
            inputSchema={
                "type": "object",
                "properties": {
                    "x1": {"type": "number", "default": 0, "description": "Start X"},
                    "y1": {"type": "number", "default": 0, "description": "Start Y"},
                    "x2": {"type": "number", "default": 100, "description": "End X"},
                    "y2": {"type": "number", "default": 0, "description": "End Y"},
                    "unit": {"type": "string", "description": "Unit (mm, inch, m)"},
                    "construction": {"type": "boolean", "default": False, "description": "Draw as construction geometry (centerline)"}
                },
                "required": []
            }
        ),
        Tool(
            name="draw_circle",
            description="Draw a circle in the active sketch.",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "number", "default": 0, "description": "Center X"},
                    "y": {"type": "number", "default": 0, "description": "Center Y"},
                    "radius": {"type": "number", "default": 25, "description": "Radius"},
                    "unit": {"type": "string", "description": "Unit (mm, inch, m)"}
                },
                "required": []
            }
        ),
        Tool(
            name="draw_rectangle",
            description="Draw a rectangle in the active sketch.",
            inputSchema={
                "type": "object",
                "properties": {
                    "x1": {"type": "number", "default": -50, "description": "First corner X"},
                    "y1": {"type": "number", "default": -25, "description": "First corner Y"},
                    "x2": {"type": "number", "default": 50, "description": "Second corner X"},
                    "y2": {"type": "number", "default": 25, "description": "Second corner Y"},
                    "unit": {"type": "string", "description": "Unit (mm, inch, m)"}
                },
                "required": []
            }
        ),
        Tool(
            name="draw_arc",
            description="Draw an arc by center and angles.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cx": {"type": "number", "default": 0, "description": "Center X"},
                    "cy": {"type": "number", "default": 0, "description": "Center Y"},
                    "radius": {"type": "number", "default": 25, "description": "Radius"},
                    "start_angle": {"type": "number", "default": 0, "description": "Start angle (degrees)"},
                    "end_angle": {"type": "number", "default": 90, "description": "End angle (degrees)"},
                    "unit": {"type": "string", "description": "Unit for radius"}
                },
                "required": []
            }
        ),
        Tool(
            name="draw_polygon",
            description="Draw a regular polygon.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cx": {"type": "number", "default": 0, "description": "Center X"},
                    "cy": {"type": "number", "default": 0, "description": "Center Y"},
                    "radius": {"type": "number", "default": 25, "description": "Radius"},
                    "sides": {"type": "integer", "default": 6, "description": "Number of sides (3-100)"},
                    "unit": {"type": "string", "description": "Unit"}
                },
                "required": []
            }
        ),
        Tool(
            name="draw_text",
            description="Draw 2D sketch text in the active sketch to create extruded 3D typography or engraved labels.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to insert (supports \\n for multi-line)"},
                    "x": {"type": "number", "default": 0, "description": "X coordinate for text placement"},
                    "y": {"type": "number", "default": 0, "description": "Y coordinate for text placement"},
                    "height": {"type": "number", "default": 12, "description": "Font character height in units (default 12mm)"},
                    "font_name": {"type": "string", "default": "Segoe UI", "description": "Font typeface name (default 'Segoe UI')"},
                    "bold": {"type": "boolean", "default": True, "description": "Bold text style"},
                    "italic": {"type": "boolean", "default": False, "description": "Italic text style"},
                    "angle": {"type": "number", "default": 0, "description": "Text rotation angle in degrees (e.g. 0, 90, 180, 270)"},
                    "center": {"type": "boolean", "default": False, "description": "Center-align text horizontally"},
                    "unit": {"type": "string", "description": "Unit for coordinates and height (e.g. 'mm')"}
                },
                "required": ["text"]
            }
        ),
        
        # Feature Tools
        Tool(
            name="extrude_sketch",
            description="Extrude the active sketch (Boss-Extrude).",
            inputSchema={
                "type": "object",
                "properties": {
                    "depth": {"type": "number", "default": 10, "description": "Extrusion depth"},
                    "both_directions": {"type": "boolean", "default": False, "description": "Extrude in both directions"},
                    "reverse_dir": {"type": "boolean", "default": False, "description": "Reverse extrusion direction (extrude backward)"},
                    "unit": {"type": "string", "description": "Unit"}
                },
                "required": []
            }
        ),
        Tool(
            name="cut_extrude",
            description="Cut extrude to remove material.",
            inputSchema={
                "type": "object",
                "properties": {
                    "depth": {"type": "number", "default": 10, "description": "Cut depth"},
                    "through_all": {"type": "boolean", "default": False, "description": "Cut through all"},
                    "both_directions": {"type": "boolean", "default": False, "description": "Cut both directions"},
                    "unit": {"type": "string", "description": "Unit"}
                },
                "required": []
            }
        ),
        Tool(
            name="fillet_edges",
            description="Add fillet to selected edges.",
            inputSchema={
                "type": "object",
                "properties": {
                    "radius": {"type": "number", "default": 2, "description": "Fillet radius"},
                    "unit": {"type": "string", "description": "Unit"}
                },
                "required": []
            }
        ),
        Tool(
            name="chamfer_edges",
            description="Add chamfer to selected edges.",
            inputSchema={
                "type": "object",
                "properties": {
                    "distance": {"type": "number", "default": 2, "description": "Chamfer distance"},
                    "angle": {"type": "number", "default": 45, "description": "Chamfer angle (degrees)"},
                    "unit": {"type": "string", "description": "Unit"}
                },
                "required": []
            }
        ),
        Tool(
            name="get_feature_tree",
            description=(
                "Read the model's feature tree. detail='list': every feature and sub-feature with type and suppression "
                "state. detail='parametric' (.NET runner, parts only): full JSON description with typed feature "
                "parameters, global equations and sketches with closed profile loops and dimensions. detail='context' "
                "(.NET runner, fast): open documents, active document info, custom properties, current selections and a "
                "depth-limited feature/assembly/mate tree; a good first call on an unfamiliar model."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "detail": {"type": "string", "enum": ["list", "parametric", "context"], "default": "list", "description": "How much to return"},
                    "depth": {"type": "integer", "default": 2, "minimum": 0, "maximum": 5, "description": "context only: tree expansion depth (0 = summary)"},
                    "target_path": {"type": "string", "description": "context only: slash-delimited feature/component path to expand"},
                    "document_name": {"type": "string", "description": "context only: open document title to inspect without activating it"}
                },
                "required": []
            }
        ),
        Tool(
            name="get_dimensions",
            description="Get dimensions from the active model. Returns full name (D1@Sketch1), value in document units, owner feature, and type.",
            inputSchema={
                "type": "object",
                "properties": {
                    "feature_name": {"type": "string", "description": "Optional feature name to filter by"},
                    "dimension_name": {"type": "string", "description": "Optional dimension name to filter by"}
                },
                "required": []
            }
        ),
        Tool(
            name="set_dimension",
            description="Set a dimension value using SystemValue conventions (meters for linear, radians for angular). Supports explicit configuration scope, rebuild, error check, and rollback.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Full name of the dimension, e.g. 'D1@Sketch1' or 'D1@Boss-Extrude1'"},
                    "value": {"type": "number", "description": "Target numeric value"},
                    "units": {"type": "string", "description": "Optional unit string ('mm', 'in', 'deg', 'rad', etc.)"},
                    "config_scope": {
                        "type": "string",
                        "enum": ["this_configuration", "all_configurations", "specific_configurations"],
                        "default": "this_configuration",
                        "description": "Configuration scope for dimension change"
                    }
                },
                "required": ["name", "value"]
            }
        ),
        Tool(
            name="manage_feature",
            description="Manage feature tree operations: suppress, unsuppress, rename, delete, reorder, set_color.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["suppress", "unsuppress", "rename", "delete", "reorder", "set_color"],
                        "description": "Feature operation action"
                    },
                    "feature_name": {"type": "string", "description": "Target feature name"},
                    "new_name": {"type": "string", "description": "New feature name for 'rename' action"},
                    "target_feature": {"type": "string", "description": "Target feature name for 'reorder' action"},
                    "move_location": {
                        "type": "string",
                        "enum": ["before", "after", "to_top", "to_end"],
                        "default": "after",
                        "description": "Relative placement for 'reorder' action"
                    },
                    "delete_absorbed": {
                        "type": "boolean",
                        "default": True,
                        "description": "Delete absorbed sketches/features on delete"
                    },
                    "delete_children": {
                        "type": "boolean",
                        "default": False,
                        "description": "Delete child features on delete"
                    },
                    "config_scope": {
                        "type": "integer",
                        "enum": [1, 2],
                        "default": 1,
                        "description": "Suppression scope: 1=ThisConfiguration, 2=AllConfigurations"
                    },
                    "color_rgb": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "RGB color [R, G, B] (0-255) for 'set_color' action"
                    }
                },
                "required": ["action", "feature_name"]
            }
        ),
        Tool(
            name="revolve_sketch",
            description="Revolve the active sketch around a centerline or specified axis (IFeatureManager.FeatureRevolve2).",
            inputSchema={
                "type": "object",
                "properties": {
                    "angle": {"type": "number", "default": 360.0, "description": "Revolution angle in degrees"},
                    "reverse_dir": {"type": "boolean", "default": False, "description": "Reverse revolution direction"},
                    "is_thin": {"type": "boolean", "default": False, "description": "Create thin feature"},
                    "thin_thickness": {"type": "number", "default": 1.0, "description": "Wall thickness for thin revolve"},
                    "is_cut": {"type": "boolean", "default": False, "description": "Revolve cut instead of boss"},
                    "axis_name": {"type": "string", "description": "Optional name of edge/axis to revolve around"},
                    "unit": {"type": "string", "default": "mm", "enum": ["mm", "inch", "m", "cm"], "description": "Length unit"}
                },
                "required": []
            }
        ),
        Tool(
            name="create_shell",
            description="Create a shell feature hollowing out the part with specified wall thickness (IModelDoc2.InsertFeatureShell).",
            inputSchema={
                "type": "object",
                "properties": {
                    "thickness": {"type": "number", "default": 1.0, "description": "Shell wall thickness"},
                    "outward": {"type": "boolean", "default": False, "description": "Shell outward from body"},
                    "unit": {"type": "string", "default": "mm", "enum": ["mm", "inch", "m", "cm"], "description": "Thickness unit"}
                },
                "required": ["thickness"]
            }
        ),
        Tool(
            name="create_pattern",
            description="Create a linear or circular pattern feature (IFeatureManager.FeatureLinearPattern4 / FeatureCircularPattern4).",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern_type": {
                        "type": "string",
                        "default": "linear",
                        "enum": ["linear", "circular"],
                        "description": "Pattern type"
                    },
                    "feature_name": {"type": "string", "description": "Name of feature to pattern (seed feature)"},
                    "num1": {"type": "integer", "default": 2, "description": "Number of instances in direction 1 (including seed)"},
                    "spacing1": {"type": "number", "default": 10.0, "description": "Spacing in direction 1"},
                    "flip_dir1": {"type": "boolean", "default": False, "description": "Flip direction 1"},
                    "num2": {"type": "integer", "default": 1, "description": "Number of instances in direction 2"},
                    "spacing2": {"type": "number", "default": 0.0, "description": "Spacing in direction 2"},
                    "flip_dir2": {"type": "boolean", "default": False, "description": "Flip direction 2"},
                    "angle": {"type": "number", "default": 360.0, "description": "Total angle for circular pattern in degrees"},
                    "equal_spacing": {"type": "boolean", "default": True, "description": "Equal spacing for circular pattern"},
                    "unit": {"type": "string", "default": "mm", "enum": ["mm", "inch", "m", "cm"], "description": "Length unit"}
                },
                "required": ["num1", "spacing1"]
            }
        ),
        Tool(
            name="create_hole_wizard",
            description="Create a standard Hole Wizard hole on the active face (IFeatureManager.HoleWizard5).",
            inputSchema={
                "type": "object",
                "properties": {
                    "hole_type": {
                        "type": "string",
                        "default": "counterbore",
                        "enum": ["counterbore", "countersink", "hole", "standard", "pipe_tap", "tap"],
                        "description": "Hole type"
                    },
                    "standard": {
                        "type": "string",
                        "default": "ISO",
                        "enum": ["ISO", "DIN", "ANSI Metric", "ANSI Inch"],
                        "description": "Fastener standard"
                    },
                    "fastener_type": {"type": "string", "default": "Hex Bolt", "description": "Fastener description"},
                    "size": {"type": "string", "default": "M6", "description": "Hole thread/clearance size (e.g. 'M6', 'M8', 'M10')"},
                    "end_condition": {
                        "type": "string",
                        "default": "through_all",
                        "enum": ["through_all", "blind"],
                        "description": "End condition"
                    },
                    "depth": {"type": "number", "description": "Hole depth for blind holes"},
                    "diameter": {"type": "number", "description": "Hole diameter override"},
                    "unit": {"type": "string", "default": "mm", "enum": ["mm", "inch", "m", "cm"], "description": "Length unit"}
                },
                "required": []
            }
        ),
        
        # Sketch Management Tools
        Tool(
            name="close_sketch",
            description="Close/exit the active sketch. Call this before extrude if sketch is still open.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="get_sketch_status",
            description="Get diagnostic info: active sketch state, sketch count, sketch names in feature tree.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        
        # Utility Tools
        Tool(
            name="set_units",
            description="Set default unit for dimensions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "unit": {
                        "type": "string",
                        "enum": ["mm", "inch", "m", "cm"],
                        "description": "Default unit"
                    }
                },
                "required": ["unit"]
            }
        ),
        Tool(
            name="execute_code",
            description=(
                "Run custom code against the live SolidWorks session when no dedicated tool fits. "
                "language='python': persistent namespace with `sw`, `doc`, `automation`, `FinalAnswer(value)`; stdout is captured. "
                "language='csharp' (.NET runner): stateful Roslyn cell with pre-bound `ISldWorks swApp`, `FinalAnswer(string)`, "
                "`Print(object)`, `SharedPath(string)`, `WorkPath(string)` and automatic COM interface cast repair. "
                "language='csharp_macro' (.NET runner): a full `public class DynamicClass { public static object Execute(...) }` source. "
                "SECURITY GATE: disabled by default. Without prior consent it refuses and tells you to ask the human user. "
                "To unlock: re-invoke with user_confirmed_risks=true after the user explicitly agrees in chat, or start the "
                "server with SW_MCP_ALLOW_EXEC=1."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "language": {"type": "string", "enum": ["python", "csharp", "csharp_macro"], "description": "Language / execution mode"},
                    "code": {"type": "string", "description": "Source code to execute"},
                    "user_confirmed_risks": {
                        "type": "boolean",
                        "default": False,
                        "description": "Set true only after the human user has explicitly agreed in chat to arbitrary code execution."
                    },
                    "reset_session": {"type": "boolean", "default": False, "description": "python, csharp: clear the persistent session before running"},
                    "read_only": {
                        "type": "boolean",
                        "default": False,
                        "description": "python: roll back and fail if volume or feature count changed. C#: refuse to run code that static analysis classifies as mutating."
                    },
                    "auto_transaction": {"type": "boolean", "default": True, "description": "python: undo the script's changes if it raises"},
                    "parameters": {"type": "array", "items": {}, "description": "csharp_macro: arguments passed to DynamicClass.Execute"},
                    "timeout_ms": {"type": "integer", "default": 120000, "description": "C#: maximum execution time in milliseconds"}
                },
                "required": ["language", "code"]
            }
        ),
        
        # Inspection, Verification & Viewport Tools (Phase 2)
        Tool(
            name="inspect_model",
            description="Inspect active model geometry: body count, bounding box (dx, dy, dz), volume, surface area, center of mass, and rebuild status.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="verify_model",
            description="Automated model verification: checks rebuild clean state, solid bodies, and asserts bounding box dimensions against expected tolerances.",
            inputSchema={
                "type": "object",
                "properties": {
                    "expected_dx": {"type": "number", "description": "Expected length along X in mm"},
                    "expected_dy": {"type": "number", "description": "Expected length along Y in mm"},
                    "expected_dz": {"type": "number", "description": "Expected length along Z in mm"},
                    "expected_bodies": {"type": "integer", "default": 1, "description": "Expected solid body count"},
                    "tolerance_mm": {"type": "number", "default": 0.2, "description": "Permitted deviation in mm"}
                },
                "required": []
            }
        ),
        Tool(
            name="select",
            description=(
                "Select model entities. Choose the method with `by`: "
                "'name' - a named plane, feature or body (name), or planar faces by `direction`; "
                "'point' - the entity under a 3D `point`; "
                "'geometry' - faces/edges/vertices matching filters (surface_type, normal, radius, curve_type, length, midpoint, point, bbox); "
                "'centroid' - the face whose centroid, or edge whose midpoint, is at x/y/z in mm (indices come from list_topology); "
                "'path' - a topology path such as 'Boss-Extrude1/Face[0]/Edge[2]' (.NET runner). "
                "Prefer 'geometry' when you can describe the entity and 'centroid' when you have coordinates from list_topology."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "by": {"type": "string", "enum": ["name", "point", "geometry", "centroid", "path"], "description": "Selection method"},
                    "target_type": {
                        "type": "string",
                        "enum": ["plane", "feature", "face", "edge", "vertex", "body"],
                        "default": "face",
                        "description": "Entity category (geometry: face/edge/vertex; centroid: face/edge)"
                    },
                    "name": {"type": "string", "description": "name: entity name (e.g. 'Front Plane', 'Boss-Extrude1')"},
                    "direction": {"type": "string", "enum": ["top", "bottom", "front", "back", "left", "right"], "description": "name/point: planar face normal direction"},
                    "point": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "point: a point on the entity. geometry: the vertex location [x, y, z]"},
                    "path": {"type": "string", "description": "path: slash-delimited topology path"},
                    "x": {"type": "number", "description": "centroid: expected X in mm (omit to ignore)"},
                    "y": {"type": "number", "description": "centroid: expected Y in mm (omit to ignore)"},
                    "z": {"type": "number", "description": "centroid: expected Z in mm (omit to ignore)"},
                    "tolerance": {"type": "number", "default": 0.5, "description": "centroid: coordinate tolerance in mm"},
                    "body_index": {"type": "integer", "default": 1, "minimum": 1, "description": "centroid: 1-based solid body index"},
                    "min_area": {"type": "number", "default": 10.0, "description": "centroid: minimum face area in mm^2"},
                    "locate_only": {"type": "boolean", "default": False, "description": "centroid: return the 1-based index without selecting"},
                    "surface_type": {
                        "type": "string",
                        "enum": ["plane", "cylinder", "cone", "sphere", "torus", "bspline"],
                        "description": "geometry/centroid: surface type of the face (centroid defaults to plane)"
                    },
                    "normal": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "geometry: face normal unit vector [nx, ny, nz]"},
                    "normal_tolerance": {"type": "number", "default": 0.05, "description": "geometry: tolerance for normal matching"},
                    "radius": {"type": "number", "description": "geometry: radius of cylindrical/spherical faces or circular edges"},
                    "radius_tolerance": {"type": "number", "default": 0.05, "description": "geometry: tolerance for radius matching"},
                    "point_on_surface": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "geometry: a point that must lie on the face [x, y, z]"},
                    "curve_type": {"type": "string", "enum": ["line", "circle", "arc", "bspline"], "description": "geometry: edge curve type"},
                    "length": {"type": "number", "description": "geometry: edge length"},
                    "length_tolerance": {"type": "number", "default": 0.1, "description": "geometry: tolerance for edge length matching"},
                    "midpoint": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "geometry: edge midpoint [x, y, z]"},
                    "bbox_min": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "geometry: bounding box minimum [xmin, ymin, zmin]"},
                    "bbox_max": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "geometry: bounding box maximum [xmax, ymax, zmax]"},
                    "mark": {"type": "integer", "default": 0, "description": "Selection mark"},
                    "append": {"type": "boolean", "default": False, "description": "Add to the current selection instead of replacing it"},
                    "unit": {"type": "string", "enum": ["mm", "inch", "m", "cm"], "description": "Unit for name/point/geometry coordinates"}
                },
                "required": ["by"]
            }
        ),
        Tool(
            name="capture_viewport",
            description=(
                "Save an image of the model. mode='view': one named view (*Isometric, *Front, *Top, *Right, *Back, *Left, "
                "*Bottom or a custom view) to PNG/JPG. mode='overview' (.NET runner): a stitched seven-view sheet plus up to "
                "four zoomed detail crops. mode='showcase': set display style and an explicit camera, then capture."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["view", "overview", "showcase"], "default": "view", "description": "Capture mode"},
                    "filepath": {"type": "string", "description": "view/showcase: output image path (optional)"},
                    "view_name": {"type": "string", "default": "*Isometric", "description": "view: named view orientation"},
                    "save_dir": {"type": "string", "description": "overview: directory for overview_<ts>.png and detail_<ts>.png"},
                    "eye_mm": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "showcase: camera eye position [x, y, z] in mm"},
                    "target_mm": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "showcase: camera target point [x, y, z] in mm"},
                    "up_vec": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "showcase: camera up vector [ux, uy, uz]"},
                    "display_style": {
                        "type": "string",
                        "enum": ["shaded_with_edges", "realistic", "shaded", "wireframe", "hidden_lines_removed"],
                        "default": "shaded_with_edges",
                        "description": "showcase: viewport rendering style"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="show_interactive_studio",
            description=(
                "Open the interactive studio for the active model: dimension sliders plus a live viewport, "
                "rendered from A2UI 1.0 messages. The Claude app shows it inline; otherwise give the user the "
                "returned studioUrl to open in a browser. Slider moves rebuild the model in SolidWorks."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "view_name": {
                        "type": "string",
                        "default": "*Isometric",
                        "enum": ["*Isometric", "*Front", "*Top", "*Right", "*Back", "*Left", "*Bottom"],
                        "description": "Initial view orientation for viewport preview"
                    },
                    "feature_labels": {
                        "type": "object",
                        "description": "Optional mapping of feature names to friendly labels (e.g. {'Boss-Extrude1': 'Hex Head', 'Boss-Extrude2': 'Shaft Body'})"
                    },
                    "dimension_labels": {
                        "type": "object",
                        "description": "Optional mapping of dimension names to friendly labels (e.g. {'D1@Boss-Extrude1@Part1.Part': 'Head Thickness'})"
                    },
                    "feature_filter": {
                        "type": "string",
                        "description": "Optional feature name filter (e.g. 'Sketch1' or 'Boss-Extrude1')"
                    }
                },
                "required": []
            },
            meta={
                "ui": {
                    "resourceUri": "ui://solidworks/studio.html"
                }
            }
        ),

        Tool(
            name="studio_action",
            description=(
                "Internal to the interactive studio view: applies one user action (slider move, view change, reset) "
                "sent by the studio UI as an A2UI renderer-to-agent message. Not for direct use; change dimensions "
                "with set_dimension instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "object",
                        "description": "A2UI 1.0 renderer-to-agent message: {version, action: {name, surfaceId, sourceComponentId, timestamp, context}}"
                    }
                },
                "required": ["message"]
            },
            meta={
                "ui": {
                    "resourceUri": "ui://solidworks/studio.html",
                    # Callable by the studio view only; hidden from the model.
                    "visibility": ["app"]
                }
            }
        ),

        # Assembly Tools (Phase 3)
        Tool(
            name="add_component",
            description="Insert a component (part or subassembly) into the active assembly with 3D translation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "component_path": {"type": "string", "description": "Absolute or relative path to component model"},
                    "x": {"type": "number", "default": 0.0, "description": "X coordinate"},
                    "y": {"type": "number", "default": 0.0, "description": "Y coordinate"},
                    "z": {"type": "number", "default": 0.0, "description": "Z coordinate"},
                    "unit": {"type": "string", "default": "mm", "enum": ["mm", "inch", "m", "cm"], "description": "Coordinate unit"}
                },
                "required": ["component_path"]
            }
        ),
        Tool(
            name="add_mate",
            description="Create an assembly mate (coincident, concentric, distance, parallel, perpendicular, tangent, angle) between two entities.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mate_type": {
                        "type": "string",
                        "enum": ["coincident", "concentric", "perpendicular", "parallel", "tangent", "distance", "angle"],
                        "description": "Mate type"
                    },
                    "entity1": {"type": "string", "description": "First entity/plane/face name (e.g. 'Top Plane')"},
                    "entity2": {"type": "string", "description": "Second entity/plane/face name"},
                    "comp1": {"type": "string", "description": "Optional component name for entity1 (e.g. 'part-1')"},
                    "comp2": {"type": "string", "description": "Optional component name for entity2 (e.g. 'part-2')"},
                    "align": {"type": "integer", "default": 0, "enum": [0, 1, 2], "description": "Alignment: 0=Aligned, 1=Anti-aligned, 2=Closest"},
                    "flip": {"type": "boolean", "default": False, "description": "Flip dimension direction"},
                    "distance": {"type": "number", "default": 0.0, "description": "Distance value (for distance mate)"},
                    "angle": {"type": "number", "default": 0.0, "description": "Angle in degrees (for angle mate)"},
                    "min_limit": {"type": "number", "description": "Optional minimum limit for LimitDistance or LimitAngle mate"},
                    "max_limit": {"type": "number", "description": "Optional maximum limit for LimitDistance or LimitAngle mate"},
                    "unit": {"type": "string", "default": "mm", "enum": ["mm", "inch", "m", "cm"], "description": "Distance unit"}
                },
                "required": ["mate_type", "entity1", "entity2"]
            }
        ),
        Tool(
            name="check_interference",
            description="Check for geometric interference/collisions between components in the active assembly.",
            inputSchema={
                "type": "object",
                "properties": {
                    "treat_coincidence_as_interference": {
                        "type": "boolean",
                        "default": False,
                        "description": "Treat coincident faces as interference"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="list_components",
            description="List all components in the active assembly with suppression, fixity, and paths.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),

        # Drawing Tools (Phase 3)
        Tool(
            name="create_drawing_from_model",
            description="Create a new drawing and insert an initial model view.",
            inputSchema={
                "type": "object",
                "properties": {
                    "model_path": {"type": "string", "description": "Optional model path (defaults to active document)"},
                    "view_name": {"type": "string", "default": "*Isometric", "description": "Named view to insert"},
                    "x": {"type": "number", "default": 0.1, "description": "View X position on sheet in meters"},
                    "y": {"type": "number", "default": 0.1, "description": "View Y position on sheet in meters"},
                    "paper_size": {"type": "string", "default": "A4", "enum": ["A4", "A3", "A2", "A1", "Letter"], "description": "Sheet size"}
                },
                "required": []
            }
        ),
        Tool(
            name="export_drawing",
            description="Export the active drawing document to PDF, DXF, DWG, PNG, or JPG.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Destination file path"},
                    "format": {
                        "type": "string",
                        "default": "pdf",
                        "enum": ["pdf", "dxf", "dwg", "png", "jpg"],
                        "description": "Export format"
                    }
                },
                "required": ["filepath"]
            }
        ),
        Tool(
            name="import_model_items",
            description="Import model dimensions and annotations into drawing views (IDrawingDoc.InsertModelAnnotations3).",
            inputSchema={
                "type": "object",
                "properties": {
                    "option": {"type": "integer", "default": 1, "description": "1=All views, 2=Selected view"},
                    "types": {"type": "integer", "default": 1, "description": "1=Dimensions, 2=Notes, 4=Surface finish"},
                    "all_views": {"type": "boolean", "default": True, "description": "Import into all views"},
                    "duplicate_dims": {"type": "boolean", "default": False, "description": "Allow duplicate dimensions"},
                    "hidden_feature_dims": {"type": "boolean", "default": False, "description": "Include hidden feature dimensions"},
                    "use_placement_in_sketch": {"type": "boolean", "default": True, "description": "Use dimension sketch placement"}
                },
                "required": []
            }
        ),
        Tool(
            name="add_drawing_dimension",
            description="Add a dimension to selected entities on the active drawing sheet (IModelDoc2.AddDimension2).",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "number", "description": "Dimension text placement X on drawing sheet"},
                    "y": {"type": "number", "description": "Dimension text placement Y on drawing sheet"},
                    "z": {"type": "number", "default": 0.0, "description": "Dimension text placement Z"},
                    "unit": {"type": "string", "default": "mm", "enum": ["mm", "inch", "m", "cm"], "description": "Coordinate unit"}
                },
                "required": ["x", "y"]
            }
        ),
        Tool(
            name="create_section_view",
            description="Create a section view from an active section line sketch in the drawing (IDrawingDoc.CreateSectionViewAt5).",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "number", "description": "Section view placement X on sheet"},
                    "y": {"type": "number", "description": "Section view placement Y on sheet"},
                    "section_label": {"type": "string", "default": "A", "description": "Section label (e.g. 'A', 'B')"},
                    "options": {"type": "integer", "default": 32, "description": "Section view options bitmask (default 32: swCreateSectionView_NotAligned)"},
                    "line_x1": {"type": "number", "description": "Optional section line start X coordinate"},
                    "line_y1": {"type": "number", "description": "Optional section line start Y coordinate"},
                    "line_x2": {"type": "number", "description": "Optional section line end X coordinate"},
                    "line_y2": {"type": "number", "description": "Optional section line end Y coordinate"},
                    "parent_view": {"type": "string", "description": "Optional parent drawing view name"},
                    "unit": {"type": "string", "default": "mm", "enum": ["mm", "inch", "m", "cm"], "description": "Coordinate unit"}
                },
                "required": ["x", "y"]
            }
        ),
        Tool(
            name="insert_bom_table",
            description="Insert a Bill of Materials (BOM) table into the active drawing view (IView.InsertBomTable4 / InsertBomTable3).",
            inputSchema={
                "type": "object",
                "properties": {
                    "template_path": {"type": "string", "description": "Path to BOM table template (.sldbomtbt)"},
                    "x": {"type": "number", "default": 0.0, "description": "Table placement X coordinate"},
                    "y": {"type": "number", "default": 0.0, "description": "Table placement Y coordinate"},
                    "bom_type": {
                        "type": "string",
                        "default": "top_level",
                        "enum": ["top_level", "parts_only", "indented"],
                        "description": "BOM table type"
                    },
                    "anchor_type": {
                        "type": "integer",
                        "default": 1,
                        "description": "Anchor corner: 1=top-left, 2=top-right, 3=bottom-left, 4=bottom-right"
                    },
                    "configuration": {"type": "string", "default": "", "description": "Target configuration name"},
                    "unit": {"type": "string", "default": "mm", "enum": ["mm", "inch", "m", "cm"], "description": "Coordinate unit"}
                },
                "required": []
            }
        ),

        # Simulation Tools (Phase 4)
        Tool(
            name="create_simulation_study",
            description="Create a new FEA simulation study (static, frequency, buckling, thermal).",
            inputSchema={
                "type": "object",
                "properties": {
                    "study_name": {"type": "string", "default": "StaticStudy", "description": "Name for the study"},
                    "study_type": {
                        "type": "string",
                        "default": "static",
                        "enum": ["static", "frequency", "buckling", "thermal"],
                        "description": "Analysis type"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="set_study_material",
            description="Assign engineering material (e.g. 'AISI 1020', 'Plain Carbon Steel') to model and simulation bodies.",
            inputSchema={
                "type": "object",
                "properties": {
                    "material_name": {"type": "string", "default": "AISI 1020", "description": "Material name"},
                    "database_path": {"type": "string", "description": "Path to .sldmat database file (optional)"},
                    "config_name": {"type": "string", "default": "Default", "description": "Part configuration name"}
                },
                "required": ["material_name"]
            }
        ),
        Tool(
            name="add_study_fixture",
            description="Apply boundary restraint (fixed, immovable, roller, hinge, symmetric) to a face.",
            inputSchema={
                "type": "object",
                "properties": {
                    "fixture_type": {
                        "type": "string",
                        "default": "fixed",
                        "enum": ["fixed", "immovable", "roller", "hinge", "symmetric"],
                        "description": "Type of boundary restraint"
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["top", "bottom", "left", "right", "front", "back"],
                        "description": "Face normal direction filter"
                    },
                    "face_point": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "Face coordinate point [x, y, z]"
                    },
                    "face_name": {"type": "string", "description": "Named face or entity"},
                    "unit": {"type": "string", "default": "mm", "enum": ["mm", "inch", "m", "cm"], "description": "Coordinate unit"},
                    "study_name": {"type": "string", "description": "Optional study name"}
                },
                "required": []
            }
        ),
        Tool(
            name="add_study_load",
            description="Apply pressure or force load to a face in the simulation study.",
            inputSchema={
                "type": "object",
                "properties": {
                    "load_type": {
                        "type": "string",
                        "default": "pressure",
                        "enum": ["pressure", "force"],
                        "description": "Load type"
                    },
                    "value": {"type": "number", "default": 100000.0, "description": "Load magnitude"},
                    "unit": {
                        "type": "string",
                        "default": "N/m^2",
                        "enum": ["N/m^2", "Pa", "psi", "MPa", "N", "lbf"],
                        "description": "Unit of load"
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["top", "bottom", "left", "right", "front", "back"],
                        "description": "Face normal direction filter"
                    },
                    "face_point": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "Face coordinate point [x, y, z]"
                    },
                    "face_name": {"type": "string", "description": "Named face or entity"},
                    "coord_unit": {"type": "string", "default": "mm", "enum": ["mm", "inch", "m", "cm"], "description": "Coordinate unit"},
                    "study_name": {"type": "string", "description": "Optional study name"}
                },
                "required": ["value"]
            }
        ),
        Tool(
            name="mesh_and_run_simulation",
            description="Mesh the geometry and run the FEA simulation solver synchronously.",
            inputSchema={
                "type": "object",
                "properties": {
                    "element_size_mm": {"type": "number", "default": 2.0, "description": "Element size in mm"},
                    "tolerance_mm": {"type": "number", "default": 0.1, "description": "Mesh tolerance in mm"},
                    "study_name": {"type": "string", "description": "Optional study name"}
                },
                "required": []
            }
        ),
        Tool(
            name="get_simulation_results",
            description="Extract von Mises stress (MPa), resultant displacement (mm), and engineering sanity checks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "study_name": {"type": "string", "description": "Optional study name"}
                },
                "required": []
            }
        ),

        # Knowledge & Operational Hardening Tools (Phase 5)
        Tool(
            name="search_knowledge",
            description="Search versioned local knowledge base for verified SOLIDWORKS API behaviors and code snippets.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword or phrase"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tag filters (e.g. ['simulation', 'win32com'])"
                    },
                    "interface": {"type": "string", "description": "API interface name (e.g. 'ICWStudy', 'IFace2')"},
                    "min_confidence": {"type": "number", "default": 0.0, "description": "Minimum confidence threshold (0.0 to 1.0)"}
                },
                "required": []
            }
        ),
        Tool(
            name="record_knowledge",
            description="Record a newly discovered or verified API behavior into the versioned local knowledge base.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Unique slug identifier (e.g. 'sw2025_feature_pattern')"},
                    "title": {"type": "string", "description": "Short descriptive title"},
                    "api_interface": {"type": "string", "description": "COM interface or class name"},
                    "api_member": {"type": "string", "description": "Member method or property name"},
                    "problem": {"type": "string", "description": "Failure description or unexpected quirk"},
                    "successful_approach": {"type": "string", "description": "Verified successful pattern or solution"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for categorization"
                    },
                    "promotion_state": {
                        "type": "string",
                        "default": "discovered",
                        "enum": ["discovered", "experimentally_verified", "documented", "confirmed", "version_specific"],
                        "description": "Evidence promotion tier"
                    },
                    "confidence": {"type": "number", "default": 0.8, "description": "Confidence level (0.0 to 1.0)"}
                },
                "required": ["id", "title", "problem", "successful_approach"]
            }
        ),

        # Diagnostics & Recovery Tools
        Tool(
            name="inspect_dialogs",
            description="Inspect active SolidWorks modal dialogs, error windows, prompts, and their text messages & buttons.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="dismiss_dialog",
            description="Dismiss a SolidWorks dialog window by clicking a specified button (e.g. 'OK', 'Cancel', 'Yes', 'No', 'Close', 'Rebuild') or sending a close signal. NOTE: If an operation timed out while blocked by this dialog, the abandoned call may still execute once unblocked and mutate the model (inspect state before retrying). New tool calls remain blocked until the modal dialog is dismissed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer", "description": "Window handle (HWND) of the dialog. If omitted, targets the first active popup."},
                    "action": {"type": "string", "default": "OK", "description": "Button text or action (e.g. 'OK', 'Cancel', 'Yes', 'No', 'Close', 'Rebuild')"},
                    "force_close": {"type": "boolean", "default": False, "description": "Send WM_CLOSE directly to the window"}
                },
                "required": []
            }
        ),
        Tool(
            name="load_addin",
            description="Dynamically load and activate a SolidWorks add-in at runtime (e.g. 'simulation', 'toolbox', 'routing', 'motion', 'photoview', or path to DLL).",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Add-in nickname ('simulation', 'toolbox', 'routing', 'motion', 'photoview') or full path to DLL"}
                },
                "required": ["name"]
            }
        ),
        Tool(
            name="inspect_rebuild_errors",
            description="Inspect active document feature tree for rebuild errors, overdefined or dangling sketch relations, and broken assembly mates without opening UI dialogs.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="reset_sta_worker",
            description="Forcefully reset and restart the Single-Threaded Apartment (STA) COM worker thread to recover from permanently hung or abandoned calls. NOTE: (a) The abandoned call may still complete later if SolidWorks unblocks, and may mutate the model (inspect model/document state before retrying mutating tools). (b) If SolidWorks itself is blocked by an active modal dialog, calls dispatched to the newly created STA worker will also detect the modal dialog and block until dismissed.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),

        # MecAgent .NET 10 Runner & C# CAD SDK Tools
        Tool(
            name="inspect_topology",
            description=(
                "Inspect the model's B-Rep and feature hierarchy at a slash-delimited path (.NET runner). '' or '/' gives "
                "the root overview, 'FeatureName' its faces/edges/parameters, 'FeatureName/Face[0]' the face area, normal, "
                "UV bounds and adjacent edges with dihedral angles. With edges=true, returns every edge of the face at "
                "`path` (or of the selected face if path is empty) with type, start/end/mid coordinates in mm and length."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "", "description": "Slash-delimited path to inspect"},
                    "show_equations": {"type": "boolean", "default": False, "description": "Include equation details for the root or a feature"},
                    "edges": {"type": "boolean", "default": False, "description": "Analyze the edges of the face at `path`"}
                },
                "required": []
            }
        ),
        Tool(
            name="find_related_files",
            description=(
                "Discover all related CAD files (parts, sub-assemblies, parent assemblies, and referencing `.SLDDRW` "
                "drawings) for the active document via `CadDataDiscoveryController.FindRelatedFiles`, including "
                "recursive `GetDocumentDependencies2` traversal, backward drawing discovery, OneDrive cloud-placeholder "
                "detection, and `SwDocumentMgr` Toolbox fastener filtering."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "max_drawings_probed": {
                        "type": "integer",
                        "default": 200,
                        "description": "Maximum candidate `.SLDDRW` files to probe during backward drawing search"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="modify_feature_by_path",
            description=(
                "Modify feature parameters directly by feature path via `SolidworksModificationController.ModifyFeatureParameterByPath` "
                "(e.g. extrusion depth_mm, reverse_direction, draft_enabled, draft_angle_deg, or fillet_radius_mm) with automatic rebuild."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "feature_path": {"type": "string", "description": "Slash-delimited feature path (e.g. 'Boss-Extrude1' or 'Fillet1')"},
                    "depth_mm": {"type": "number", "description": "New extrusion/cut depth in mm"},
                    "reverse_direction": {"type": "boolean", "description": "Reverse extrusion/cut direction"},
                    "draft_enabled": {"type": "boolean", "description": "Enable/disable draft on extrusion"},
                    "draft_angle_deg": {"type": "number", "description": "Draft angle in degrees"},
                    "fillet_radius_mm": {"type": "number", "description": "New constant fillet radius in mm"}
                },
                "required": ["feature_path"]
            }
        ),
        # lminventor Parity Tools: Sketch, Holes, Parameters, Compact Topology, Sheet Metal, Kinematics, Supervisor
        Tool(
            name="draw_closed_profile",
            description="Draw a closed polygon/profile loop from a list of [x, y] points in a single call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "points": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 2,
                            "maxItems": 2
                        },
                        "minItems": 3,
                        "description": "Ordered list of [x, y] coordinates forming a closed loop"
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["mm", "inch", "m", "cm"],
                        "description": "Coordinate unit (default: active unit, usually mm)"
                    }
                },
                "required": ["points"]
            }
        ),
        Tool(
            name="hole_linear",
            description=(
                "Place a hole on a planar face (face_axis == face_value) positioned parametrically at dist1 from "
                "ref1_axis==ref1_value and dist2 from ref2_axis==ref2_value. Verifies material removal via volume delta."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "face_axis": {"type": "string", "enum": ["x", "y", "z", "X", "Y", "Z"], "description": "Normal axis of target planar face"},
                    "face_value": {"type": "number", "description": "Coordinate value (mm) of the planar face along face_axis"},
                    "ref1_axis": {"type": "string", "enum": ["x", "y", "z", "X", "Y", "Z"], "description": "First reference edge constant axis"},
                    "ref1_value": {"type": "number", "description": "Coordinate (mm) of first reference edge"},
                    "dist1": {"type": "number", "description": "Distance (mm) from first reference edge"},
                    "ref2_axis": {"type": "string", "enum": ["x", "y", "z", "X", "Y", "Z"], "description": "Second reference edge constant axis"},
                    "ref2_value": {"type": "number", "description": "Coordinate (mm) of second reference edge"},
                    "dist2": {"type": "number", "description": "Distance (mm) from second reference edge"},
                    "diameter": {"type": "number", "default": 5.5, "description": "Hole diameter in mm"},
                    "depth": {"type": "number", "default": 0.0, "description": "Hole depth in mm (0 = through-all)"},
                    "tapped": {"type": "boolean", "default": False, "description": "Whether hole is tapped"},
                    "cbore_diameter": {"type": "number", "default": 0.0, "description": "Counterbore diameter in mm (0 = none)"},
                    "cbore_depth": {"type": "number", "default": 0.0, "description": "Counterbore depth in mm"}
                },
                "required": ["face_axis", "face_value", "ref1_axis", "ref1_value", "dist1", "ref2_axis", "ref2_value", "dist2"]
            }
        ),
        Tool(
            name="manage_parameters",
            description=(
                "Global variables and equations. 'list': all global variables, equations and model dimensions. "
                "'add': a new global variable or equation (name='Width', expression='100mm'). "
                "'set': change an existing variable, equation or dimension expression and rebuild."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "set"], "default": "list", "description": "Parameter action"},
                    "name": {"type": "string", "description": "add/set: parameter or dimension name (e.g. 'Width' or 'D1@Sketch1')"},
                    "expression": {"type": "string", "description": "add/set: value or formula (e.g. '100mm' or '\"Width\" * 2')"},
                    "units": {"type": "string", "default": "mm", "description": "Units (default: mm)"}
                },
                "required": []
            }
        ),
        Tool(
            name="list_topology",
            description=(
                "Compact one-line-per-entity table of a body's faces or edges with 1-based indices. "
                "Faces: '[i] Plane A=90000 c=(0.0,225.0,12.5) n=(0,0,1)'. Edges: '[i] L=200.0 mid=(0.0,-225.0,0.8)'. "
                "Use the indices for create_sketch face_index and flange edge_indices, and the coordinates for select by='centroid'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["faces", "edges"], "description": "What to list"},
                    "body_index": {"type": "integer", "default": 1, "minimum": 1, "description": "1-based solid body index"},
                    "min_size": {"type": "number", "default": 0.0, "description": "Minimum face area in mm^2, or minimum edge length in mm"},
                    "max_count": {"type": "integer", "default": 60, "minimum": 1, "description": "Maximum entities to list"}
                },
                "required": ["kind"]
            }
        ),
        Tool(
            name="set_sheet_metal_thickness",
            description="Set the active sheet-metal thickness (mm), optional bend radius, and K-factor.",
            inputSchema={
                "type": "object",
                "properties": {
                    "thickness_mm": {"type": "number", "description": "Sheet thickness in mm"},
                    "bend_radius_mm": {"type": "number", "description": "Optional inner bend radius in mm"},
                    "k_factor": {"type": "number", "default": 0.5, "description": "Neutral axis K-factor"}
                },
                "required": ["thickness_mm"]
            }
        ),
        Tool(
            name="sheet_metal_face",
            description="Create a base sheet-metal Face (Base-Flange) from the active or last closed sketch profile.",
            inputSchema={
                "type": "object",
                "properties": {
                    "thickness_mm": {"type": "number", "description": "Optional override thickness in mm"},
                    "bend_radius_mm": {"type": "number", "description": "Optional override bend radius in mm"},
                    "reverse_dir": {"type": "boolean", "default": False, "description": "Reverse thickness direction"}
                },
                "required": []
            }
        ),
        Tool(
            name="flange",
            description="Add a Sheet Metal Flange of height 'distance' (mm) at bend 'angle' (deg) on 1-based edge_indices.",
            inputSchema={
                "type": "object",
                "properties": {
                    "edge_indices": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1},
                        "description": "1-based edge indices on body"
                    },
                    "distance": {"type": "number", "default": 25.0, "description": "Flange height in mm"},
                    "angle": {"type": "number", "default": 90.0, "description": "Bend angle in degrees"},
                    "body_index": {"type": "integer", "default": 1, "minimum": 1, "description": "1-based body index"}
                },
                "required": ["edge_indices"]
            }
        ),
        Tool(
            name="sheet_metal_cut",
            description="Cut perpendicular through the sheet-metal part using the active or last sketch profile.",
            inputSchema={
                "type": "object",
                "properties": {
                    "through_all": {"type": "boolean", "default": True, "description": "Cut through entire sheet"},
                    "depth": {"type": "number", "description": "Optional finite cut depth in mm"},
                    "normal_cut": {"type": "boolean", "default": True, "description": "Ensure normal cut perpendicular to sheet face"}
                },
                "required": []
            }
        ),
        Tool(
            name="delete_assembly_constraint",
            description="Delete a specific assembly mate / constraint by name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "constraint_name": {"type": "string", "description": "Name of the mate to delete (e.g. 'Coincident1')"}
                },
                "required": ["constraint_name"]
            }
        ),
        Tool(
            name="manage_subassembly_kinematics",
            description=(
                "Manage component/subassembly kinematic states: 'set_grounded' (fix), 'set_floating' (unfix), "
                "'set_flexible', 'set_rigid', or 'enforce_rigidity' (fix all static components except exclude_keywords)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["set_grounded", "set_floating", "set_flexible", "set_rigid", "enforce_rigidity", "fix", "unfix"],
                        "default": "set_flexible",
                        "description": "Kinematic action to apply"
                    },
                    "occurrence_name": {"type": "string", "description": "Target component/subassembly occurrence name"},
                    "exclude_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords of moving components to leave floating during enforce_rigidity"
                    }
                },
                "required": ["action"]
            }
        ),
        Tool(
            name="inspect_assembly_kinematics",
            description="Audit assembly hierarchy for fixed vs floating components, rigid vs flexible subassemblies, and mate health.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="drive_kinematic_motion",
            description="Interpolate a driving mate dimension from start_value to end_value across steps with live rebuilds and optional collision check.",
            inputSchema={
                "type": "object",
                "properties": {
                    "constraint_name": {"type": "string", "description": "Driving mate or dimension name (e.g. 'Distance1' or 'D1@Angle1')"},
                    "start_value": {"type": "number", "default": 0.0, "description": "Starting dimension value"},
                    "end_value": {"type": "number", "default": 90.0, "description": "Ending dimension value"},
                    "steps": {"type": "integer", "default": 10, "minimum": 1, "description": "Number of interpolation steps"},
                    "unit": {"type": "string", "default": "mm", "enum": ["mm", "deg", "inch", "m", "cm"], "description": "Dimension unit"},
                    "check_interference": {"type": "boolean", "default": False, "description": "Run collision check at each step"}
                },
                "required": ["constraint_name", "start_value", "end_value"]
            }
        ),
        Tool(
            name="manage_positional_representations",
            description="Manage named mechanism states (Positional Representations) via Assembly Configurations and mate overrides ('list', 'create', 'activate', 'set_override').",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "create", "activate", "set_override"],
                        "default": "list",
                        "description": "Positional representation action"
                    },
                    "name": {"type": "string", "description": "Representation / configuration name (e.g. 'Porta_Aberta_Max')"},
                    "constraint_name": {"type": "string", "description": "Target mate/constraint name for 'set_override'"},
                    "value": {"type": "string", "description": "Override dimension value (e.g. '90 deg' or '380 mm')"},
                    "suppress_constraint": {"type": "boolean", "description": "Optional mate suppression state in this representation"}
                },
                "required": ["action"]
            }
        ),
        Tool(
            name="apply_material_appearance",
            description="Apply a named material appearance ('Gold', 'Smooth - White', 'Clear Glass', 'Aluminum - Polished', etc.) or custom RGB/reflectivity/transparency.",
            inputSchema={
                "type": "object",
                "properties": {
                    "appearance_name": {"type": "string", "default": "Smooth - White", "description": "Preset appearance name"},
                    "color_rgb": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0, "maximum": 255},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "Optional custom [R, G, B] override"
                    },
                    "reflectivity": {"type": "number", "minimum": 0.0, "maximum": 1.0, "description": "Optional specular reflectivity (0.0–1.0)"},
                    "transparency": {"type": "number", "minimum": 0.0, "maximum": 1.0, "description": "Optional transparency (0.0–1.0)"},
                    "target_type": {"type": "string", "enum": ["part", "feature", "occurrence", "component"], "default": "part", "description": "Scope to apply appearance"},
                    "target_name": {"type": "string", "description": "Feature or component name when target_type != 'part'"}
                },
                "required": []
            }
        ),
        Tool(
            name="supervisor_clean_processes",
            description="Audit all running SolidWorks processes, identify orphaned headless zombies (not in ROT or no window), and optionally terminate them when confirm=True.",
            inputSchema={
                "type": "object",
                "properties": {
                    "confirm": {"type": "boolean", "default": False, "description": "Set True to terminate orphaned zombie processes; False for dry-run audit"},
                    "target_pids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Optional explicit list of PIDs to terminate when confirm=True"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="transaction",
            description=(
                "Protect multi-step edits. 'begin' / 'commit' / 'abort' bracket a batch with volume-delta tracking: abort "
                "deletes features added since begin and undoes other geometry changes, and reports failure if the "
                "geometry could not be restored (renames and other non-geometric edits are not rolled back). "
                "'checkpoint' records a named point in the feature tree and saves a backup copy of the document; "
                "'rollback' deletes every feature added after that checkpoint."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["begin", "commit", "abort", "checkpoint", "rollback"],
                        "default": "begin",
                        "description": "Transaction action"
                    },
                    "name": {"type": "string", "description": "Transaction label, or the checkpoint name (required for checkpoint/rollback)"},
                    "description": {"type": "string", "description": "checkpoint: optional note"}
                },
                "required": []
            }
        ),
        Tool(
            name="reload_api",
            description="Hot-reload solidworks_mcp Python modules in-place without dropping the active SolidWorks COM session.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
    ]

    for t in tools:
        schema = t.input_schema
        schema["additionalProperties"] = False
        for p_name, p_spec in schema.get("properties", {}).items():
            if p_name in ("radius", "depth", "distance", "thickness") and p_spec.get("type") == "number":
                if t.name in ("hole_linear", "add_mate", "sheet_metal_cut") and p_name in ("depth", "distance"):
                    p_spec["minimum"] = 0
                else:
                    p_spec["exclusiveMinimum"] = 0
            is_length_unit = (
                (p_name == "unit" and t.name not in _NON_LENGTH_UNIT_TOOLS)
                or p_name == "coord_unit"
            )
            if is_length_unit:
                # No schema default: the server substitutes the session unit (see set_units).
                p_spec.pop("default", None)
                p_spec["description"] = _LENGTH_UNIT_HINT
    return tools


@lru_cache(maxsize=1)
def tool_definitions() -> List[Tool]:
    """The tool catalog (built once)."""
    return _build_tools()


@lru_cache(maxsize=1)
def _validators() -> Dict[str, Draft202012Validator]:
    return {t.name: Draft202012Validator(t.input_schema) for t in tool_definitions()}


def length_unit_arguments(name: str) -> List[str]:
    """Names of the arguments of a tool that carry a length unit."""
    tool = next((t for t in tool_definitions() if t.name == name), None)
    if tool is None:
        return []
    props = tool.input_schema.get("properties", {})
    return [
        p for p in ("unit", "coord_unit")
        if p in props and not (p == "unit" and name in _NON_LENGTH_UNIT_TOOLS)
    ]


def validate_tool_arguments(name: str, arguments: Dict[str, Any]) -> Optional[str]:
    """Validate arguments against the tool's input schema. Returns an error string or None."""
    validator = _validators().get(name)
    if validator is None:
        return f"Unknown tool: {name}"
    errors = sorted(validator.iter_errors(arguments), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    return "; ".join(
        f"{'.'.join(str(p) for p in e.absolute_path) or 'arguments'}: {e.message}" for e in errors
    )
