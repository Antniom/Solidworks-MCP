"""
SolidWorks MCP Server
---------------------
Main MCP server entry point with all tools.

Version: 4.0.0 (Fixed for SolidWorks 2025)
Author: António Coelho

Fixes v4.0.0:
- execute_python now captures stdout/stderr
- FeatureExtrusion2 with correct 23 params for SW 2025
- list_features: property access instead of method calls
- extrude_sketch: proper sketch close + select before extrude
- cut_extrude: proper sketch handling
- NEW: close_sketch tool
- NEW: get_sketch_status tool for diagnostics
"""

import os
import io
import sys
import asyncio
import threading
import json
import logging
import traceback
from typing import Any, Dict, Tuple, Optional
from pathlib import Path

# MCP imports
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    Resource,
    ListResourcesResult,
    ReadResourceResult,
    ReadResourceRequestParams,
    TextResourceContents,
)
from jsonschema import Draft202012Validator

import time
import uuid
import datetime
import base64
import atexit

# Local imports
from .automation import SolidWorksAutomation
from .constants import SwErrors
from .config import get_config, save_config
from .utils import get_solidworks_info, set_default_unit
from .core import (
    ResponseEnvelope,
    create_envelope,
    success_envelope,
    error_envelope,
    get_sta_worker,
    SolidWorksModalBlockedException,
    AbandonedCallRunningException,
    CapabilityInspector,
    get_knowledge_manager,
    KnowledgeRecord,
    PromotionState,
    get_checkpoint_manager,
    get_attr_or_call,
    find_blocking_modal_dialog,
)

# Configure logging
config = get_config()
LOG_FILE = Path(__file__).parent / config.log_file

logging.basicConfig(
    level=config.get_log_level_int(),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8')]
)
logger = logging.getLogger("SolidWorksMCP")

# ============================================================================
# Global Instances
# ============================================================================

sw_automation = SolidWorksAutomation()


# ============================================================================
# Tool Definitions
# ============================================================================

def _tool_definitions() -> list[Tool]:
    """List all available SolidWorks tools"""
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
            name="get_solidworks_info",
            description="Get SolidWorks installation information.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="get_capabilities",
            description="Get comprehensive SolidWorks system capabilities, add-in status, and STA runtime diagnostics.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        
        # Document Tools
        Tool(
            name="create_new_part",
            description="Create a new part document.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="create_new_assembly",
            description="Create a new assembly document.",
            inputSchema={"type": "object", "properties": {}, "required": []}
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
            description="Create a new sketch on a plane.",
            inputSchema={
                "type": "object",
                "properties": {
                    "plane": {
                        "type": "string",
                        "enum": ["Front", "Top", "Right"],
                        "default": "Front",
                        "description": "Plane to sketch on"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="create_sketch_on_face",
            description="Create a new sketch on an existing body face (by 3D coordinates). Use for cut-extrude on faces instead of reference planes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "number", "default": 0, "description": "X coordinate on the face"},
                    "y": {"type": "number", "default": 0, "description": "Y coordinate on the face"},
                    "z": {"type": "number", "default": 0, "description": "Z coordinate on the face"},
                    "unit": {"type": "string", "description": "Unit (mm, inch, m)"}
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
            name="list_features",
            description="List all features in the model.",
            inputSchema={"type": "object", "properties": {}, "required": []}
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
            name="execute_python",
            description=(
                "Execute custom Python code with stdout capture and direct access to SolidWorks COM API ('sw', 'doc', 'automation'). "
                "SECURITY GATE: Disabled by default. If invoked without prior confirmation, it refuses execution and returns a "
                "structured prompt instructing the calling AI agent to ask the human user for explicit consent. "
                "Benefits: Direct access to full SolidWorks COM API (sw_app, model, SelectionMgr) for custom macros and niche APIs. "
                "Risks: Arbitrary Python code execution on host Windows machine with current user privileges. "
                "To unlock: set environment variable SW_MCP_ALLOW_EXEC=1, enable in config, or re-invoke with user_confirmed_risks=true "
                "after user explicitly agrees in chat."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                    "user_confirmed_risks": {
                        "type": "boolean",
                        "description": (
                            "Explicit affirmative confirmation from the human user in chat acknowledging arbitrary Python execution risks and benefits."
                        ),
                        "default": False
                    }
                },
                "required": ["code"]
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
            name="select_entity",
            description="Declarative entity selection by name, 3D coordinate point, or geometric direction/normal.",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_type": {
                        "type": "string",
                        "enum": ["plane", "feature", "face", "edge", "vertex", "body"],
                        "default": "face",
                        "description": "Entity category"
                    },
                    "name": {"type": "string", "description": "Entity name (e.g. 'Front Plane', 'Boss-Extrude1')"},
                    "x": {"type": "number", "description": "Target X coordinate"},
                    "y": {"type": "number", "description": "Target Y coordinate"},
                    "z": {"type": "number", "description": "Target Z coordinate"},
                    "unit": {"type": "string", "default": "mm", "enum": ["mm", "inch", "m", "cm"], "description": "Coordinate unit"},
                    "direction": {"type": "string", "enum": ["top", "bottom", "front", "back", "left", "right"], "description": "Planar face normal direction"},
                    "mark": {"type": "integer", "default": 0, "description": "Selection mark"},
                    "append": {"type": "boolean", "default": False, "description": "Append to current selection"}
                },
                "required": ["target_type"]
            }
        ),
        Tool(
            name="select_by_geometry",
            description="Select model entities (faces, edges, vertices) using geometric filters such as surface type, face normal, radius, edge curve type, edge length, midpoint, point, and bounding box.",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_type": {
                        "type": "string",
                        "enum": ["face", "edge", "vertex"],
                        "default": "face",
                        "description": "Target entity category"
                    },
                    "surface_type": {
                        "type": "string",
                        "enum": ["plane", "cylinder", "cone", "sphere", "torus", "bspline"],
                        "description": "Surface geometry filter (for faces)"
                    },
                    "normal": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "Target face normal unit vector [nx, ny, nz] (for planar faces)"
                    },
                    "normal_tolerance": {
                        "type": "number",
                        "default": 0.05,
                        "description": "Tolerance for normal matching"
                    },
                    "radius": {
                        "type": "number",
                        "description": "Radius for cylindrical/spherical faces or circular edges"
                    },
                    "radius_tolerance": {
                        "type": "number",
                        "default": 0.05,
                        "description": "Tolerance for radius matching"
                    },
                    "point_on_surface": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "3D coordinate that must lie on target face [x, y, z]"
                    },
                    "curve_type": {
                        "type": "string",
                        "enum": ["line", "circle", "arc", "bspline"],
                        "description": "Curve type filter (for edges)"
                    },
                    "length": {
                        "type": "number",
                        "description": "Edge length filter"
                    },
                    "length_tolerance": {
                        "type": "number",
                        "default": 0.1,
                        "description": "Tolerance for edge length matching"
                    },
                    "midpoint": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "Target edge midpoint [x, y, z]"
                    },
                    "point": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "Target vertex point [x, y, z]"
                    },
                    "bbox_min": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "Bounding box minimum [xmin, ymin, zmin]"
                    },
                    "bbox_max": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "Bounding box maximum [xmax, ymax, zmax]"
                    },
                    "mark": {
                        "type": "integer",
                        "default": 0,
                        "description": "Selection mark"
                    },
                    "append": {
                        "type": "boolean",
                        "default": False,
                        "description": "Append to current selection (False clears previous selection)"
                    },
                    "unit": {
                        "type": "string",
                        "default": "mm",
                        "enum": ["mm", "inch", "m", "cm"],
                        "description": "Coordinate/length/radius unit"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="capture_viewport",
            description="Capture high-resolution image of the 3D viewport to a PNG or JPG file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Output image path (optional)"},
                    "view_name": {"type": "string", "default": "*Isometric", "description": "Named view orientation"}
                },
                "required": []
            }
        ),
        Tool(
            name="show_interactive_studio",
            description="Open an interactive CAD studio in chat with parametric dimension sliders, live viewport preview, and instantaneous SolidWorks rebuilds.",
            inputSchema={
                "type": "object",
                "properties": {
                    "view_name": {
                        "type": "string",
                        "default": "*Isometric",
                        "enum": ["*Isometric", "*Front", "*Top", "*Right", "*Bottom", "*Left", "*Back"],
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
                    "resourceUri": "ui://solidworks/app.html"
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
            name="create_new_drawing",
            description="Create a new 2D drawing document with specified sheet size.",
            inputSchema={
                "type": "object",
                "properties": {
                    "paper_size": {
                        "type": "string",
                        "default": "A4",
                        "enum": ["A4", "A3", "A2", "A1", "Letter"],
                        "description": "Paper sheet size"
                    }
                },
                "required": []
            }
        ),
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
        Tool(
            name="create_checkpoint",
            description="Create a named modeling checkpoint (feature tree watermark & file snapshot) to protect against failures.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Unique identifier for this checkpoint"},
                    "description": {"type": "string", "description": "Optional notes on modeling progress"}
                },
                "required": ["name"]
            }
        ),
        Tool(
            name="rollback_checkpoint",
            description="Roll back the model to a previous checkpoint, deleting failed features and restoring clean geometry.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the checkpoint to restore"}
                },
                "required": ["name"]
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
    ]

    for t in tools:
        schema = getattr(t, "input_schema", getattr(t, "inputSchema", None))
        if isinstance(schema, dict):
            schema["additionalProperties"] = False
            props = schema.get("properties", {})
            for p_name, p_spec in props.items():
                if p_name in ("radius", "depth", "distance", "thickness") and p_spec.get("type") == "number":
                    p_spec["exclusiveMinimum"] = 0
    return tools


async def list_tools(
    ctx: ServerRequestContext,
    params: PaginatedRequestParams | None,
) -> ListToolsResult:
    """Return the complete baseline tool catalog for MCP SDK 2.x."""
    return ListToolsResult(tools=_tool_definitions())


# ============================================================================
# Result Formatter
# ============================================================================

def format_result(r: Dict) -> str:
    """Format result dictionary as readable text"""
    status = "SUCCESS" if r["success"] else "ERROR"
    lines = [f"[{status}] {r['message']}"]
    
    if not r["success"]:
        lines.append(f"Error Code: {r['error_code']} ({r['error_name']})")
    
    if r.get("data"):
        lines.append("Details: " + json.dumps(r["data"], indent=2))
    
    return "\n".join(lines)


# ============================================================================
# Tool Handlers
# ============================================================================

def _validate_tool_arguments(name: str, arguments: dict[str, Any]) -> str | None:
    """Restore the input-schema validation that low-level MCP SDK 1.x supplied."""
    tool = next((candidate for candidate in _tool_definitions() if candidate.name == name), None)
    if tool is None:
        return f"Unknown tool: {name}"

    errors = sorted(
        Draft202012Validator(tool.input_schema).iter_errors(arguments),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return None

    details = []
    for error in errors:
        path = ".".join(str(part) for part in error.absolute_path) or "arguments"
        details.append(f"{path}: {error.message}")
    return "; ".join(details)


def _dispatch_tool(name: str, arguments: dict) -> Dict[str, Any]:
    """Execute tool logic (dispatched entirely on the STA worker thread)."""
    if name == "connect_solidworks":
        result = sw_automation.connect(pid=arguments.get("pid"))
    
    elif name == "get_capabilities":
        caps = CapabilityInspector.get_system_capabilities(sw_automation.app)
        result = {
            "success": True,
            "message": "Capabilities discovered successfully",
            "error_code": 0,
            "error_name": "swSuccess",
            "data": caps
        }

    elif name == "get_solidworks_info":
        info = get_solidworks_info()
        result = {
            "success": info["found"],
            "message": f"SolidWorks {'found' if info['found'] else 'not found'}",
            "error_code": 0 if info["found"] else 105,
            "error_name": "swSuccess" if info["found"] else "swSolidWorksNotFound",
            "data": info
        }
    
    # Document Tools
    elif name == "create_new_part":
        result = sw_automation.create_new_part()
    
    elif name == "create_new_assembly":
        result = sw_automation.create_new_assembly()
    
    elif name == "open_document":
        result = sw_automation.open_document(arguments.get("filepath", ""))
    
    elif name == "save_document":
        result = sw_automation.save_document(arguments.get("filepath"))
    
    elif name == "close_document":
        result = sw_automation.close_document(arguments.get("save", False))
    
    elif name == "get_document_info":
        result = sw_automation.get_document_info()
    
    elif name == "list_open_documents":
        result = sw_automation.list_open_documents()
    
    elif name == "manage_configuration":
        result = sw_automation.manage_configuration(
            action=arguments.get("action", "list"),
            name=arguments.get("name"),
            new_name=arguments.get("new_name"),
            description=arguments.get("description"),
            comment=arguments.get("comment"),
        )
    
    elif name == "manage_custom_properties":
        result = sw_automation.manage_custom_properties(
            action=arguments.get("action", "list"),
            name=arguments.get("name"),
            value=arguments.get("value"),
            property_type=arguments.get("property_type", "text"),
            configuration=arguments.get("configuration", ""),
        )
    
    # Sketch Tools
    elif name == "create_sketch":
        result = sw_automation.create_sketch(arguments.get("plane", "Front"))
    
    elif name == "create_sketch_on_face":
        result = sw_automation.create_sketch_on_face(
            arguments.get("x", 0),
            arguments.get("y", 0),
            arguments.get("z", 0),
            arguments.get("unit")
        )
    
    elif name == "draw_line":
        result = sw_automation.draw_line(
            arguments.get("x1", 0),
            arguments.get("y1", 0),
            arguments.get("x2", 100),
            arguments.get("y2", 0),
            arguments.get("unit"),
            construction=arguments.get("construction", False),
        )
    
    elif name == "draw_circle":
        result = sw_automation.draw_circle(
            arguments.get("x", 0),
            arguments.get("y", 0),
            arguments.get("radius", 25),
            arguments.get("unit")
        )
    
    elif name == "draw_rectangle":
        result = sw_automation.draw_rectangle(
            arguments.get("x1", -50),
            arguments.get("y1", -25),
            arguments.get("x2", 50),
            arguments.get("y2", 25),
            arguments.get("unit")
        )
    
    elif name == "draw_arc":
        result = sw_automation.draw_arc_center(
            arguments.get("cx", 0),
            arguments.get("cy", 0),
            arguments.get("radius", 25),
            arguments.get("start_angle", 0),
            arguments.get("end_angle", 90),
            arguments.get("unit")
        )
    
    elif name == "draw_polygon":
        result = sw_automation.draw_polygon(
            arguments.get("cx", 0),
            arguments.get("cy", 0),
            arguments.get("radius", 25),
            arguments.get("sides", 6),
            arguments.get("unit")
        )
    
    elif name == "draw_text":
        result = sw_automation.draw_text(
            text=arguments.get("text", ""),
            x=arguments.get("x", 0.0),
            y=arguments.get("y", 0.0),
            height=arguments.get("height", 12.0),
            font_name=arguments.get("font_name", "Segoe UI"),
            bold=arguments.get("bold", True),
            italic=arguments.get("italic", False),
            angle=arguments.get("angle", 0.0),
            center=arguments.get("center", False),
            unit=arguments.get("unit"),
        )
    
    # Feature Tools
    elif name == "extrude_sketch":
        result = sw_automation.extrude_sketch(
            arguments.get("depth", 10),
            arguments.get("both_directions", False),
            arguments.get("reverse_dir", False),
            arguments.get("unit")
        )
    
    elif name == "cut_extrude":
        result = sw_automation.cut_extrude(
            arguments.get("depth", 10),
            arguments.get("through_all", False),
            arguments.get("both_directions", False),
            arguments.get("unit")
        )
    
    elif name == "fillet_edges":
        result = sw_automation.fillet_edges(
            arguments.get("radius", 2),
            arguments.get("unit")
        )
    
    elif name == "chamfer_edges":
        result = sw_automation.chamfer_edges(
            arguments.get("distance", 2),
            arguments.get("angle", 45),
            arguments.get("unit")
        )
    
    elif name == "list_features":
        result = _list_features_fixed()

    elif name == "get_dimensions":
        result = sw_automation.get_dimensions(
            feature_name=arguments.get("feature_name"),
            dimension_name=arguments.get("dimension_name")
        )

    elif name == "set_dimension":
        result = sw_automation.set_dimension(
            name=arguments.get("name"),
            value=arguments.get("value"),
            units=arguments.get("units"),
            config_scope=arguments.get("config_scope", "this_configuration")
        )

    elif name == "manage_feature":
        result = sw_automation.manage_feature(
            action=arguments.get("action", "suppress"),
            feature_name=arguments.get("feature_name", ""),
            new_name=arguments.get("new_name"),
            target_feature=arguments.get("target_feature"),
            move_location=arguments.get("move_location", "after"),
            delete_absorbed=arguments.get("delete_absorbed", True),
            delete_children=arguments.get("delete_children", False),
            config_scope=arguments.get("config_scope", 1),
            color_rgb=arguments.get("color_rgb"),
        )

    elif name == "revolve_sketch":
        result = sw_automation.revolve_sketch(
            angle=arguments.get("angle", 360.0),
            reverse_dir=arguments.get("reverse_dir", False),
            is_thin=arguments.get("is_thin", False),
            thin_thickness=arguments.get("thin_thickness", 1.0),
            is_cut=arguments.get("is_cut", False),
            axis_name=arguments.get("axis_name"),
            unit=arguments.get("unit", "mm"),
        )

    elif name == "create_shell":
        result = sw_automation.create_shell(
            thickness=arguments.get("thickness", 1.0),
            outward=arguments.get("outward", False),
            unit=arguments.get("unit", "mm"),
        )

    elif name == "create_pattern":
        result = sw_automation.create_pattern(
            pattern_type=arguments.get("pattern_type", "linear"),
            feature_name=arguments.get("feature_name"),
            num1=arguments.get("num1", 2),
            spacing1=arguments.get("spacing1", 10.0),
            flip_dir1=arguments.get("flip_dir1", False),
            num2=arguments.get("num2", 1),
            spacing2=arguments.get("spacing2", 0.0),
            flip_dir2=arguments.get("flip_dir2", False),
            angle=arguments.get("angle", 360.0),
            equal_spacing=arguments.get("equal_spacing", True),
            unit=arguments.get("unit", "mm"),
        )

    elif name == "create_hole_wizard":
        result = sw_automation.create_hole_wizard(
            hole_type=arguments.get("hole_type", "counterbore"),
            standard=arguments.get("standard", "ISO"),
            fastener_type=arguments.get("fastener_type", "Hex Bolt"),
            size=arguments.get("size", "M6"),
            end_condition=arguments.get("end_condition", "through_all"),
            depth=arguments.get("depth"),
            diameter=arguments.get("diameter"),
            unit=arguments.get("unit", "mm"),
        )
    
    # Sketch Management Tools
    elif name == "close_sketch":
        result = _close_sketch_handler()
    
    elif name == "get_sketch_status":
        result = _get_sketch_status_handler()
    
    # Utility Tools
    elif name == "set_units":
        unit = arguments.get("unit", "mm")
        result = sw_automation.set_units(unit)
    
    elif name == "execute_python":
        code = arguments.get("code", "")
        user_confirmed_risks = bool(arguments.get("user_confirmed_risks", False))
        if not code:
            result = sw_automation._result(False, "Code is required", SwErrors.swInvalidInput)
        else:
            result = _execute_python_fixed(code, user_confirmed_risks=user_confirmed_risks)
    
    # Inspection, Verification & Viewport Tools (Phase 2)
    elif name == "inspect_model":
        result = sw_automation.inspect_model()
    
    elif name == "verify_model":
        result = sw_automation.verify_model(
            expected_dx=arguments.get("expected_dx"),
            expected_dy=arguments.get("expected_dy"),
            expected_dz=arguments.get("expected_dz"),
            expected_bodies=arguments.get("expected_bodies", 1),
            tolerance_mm=arguments.get("tolerance_mm", 0.2),
        )
    
    elif name == "select_entity":
        pt = None
        if "x" in arguments and "y" in arguments and "z" in arguments and arguments["x"] is not None:
            pt = [arguments["x"], arguments["y"], arguments["z"]]
        result = sw_automation.select_entity(
            target_type=arguments.get("target_type", "face"),
            name=arguments.get("name"),
            point=pt,
            unit=arguments.get("unit", "mm"),
            direction=arguments.get("direction"),
            mark=arguments.get("mark", 0),
            append=arguments.get("append", False),
        )

    elif name == "select_by_geometry":
        result = sw_automation.select_by_geometry(
            target_type=arguments.get("target_type", "face"),
            surface_type=arguments.get("surface_type"),
            normal=arguments.get("normal"),
            normal_tolerance=arguments.get("normal_tolerance", 0.05),
            radius=arguments.get("radius"),
            radius_tolerance=arguments.get("radius_tolerance", 0.05),
            point_on_surface=arguments.get("point_on_surface"),
            curve_type=arguments.get("curve_type"),
            length=arguments.get("length"),
            length_tolerance=arguments.get("length_tolerance", 0.1),
            midpoint=arguments.get("midpoint"),
            point=arguments.get("point"),
            bbox_min=arguments.get("bbox_min"),
            bbox_max=arguments.get("bbox_max"),
            mark=arguments.get("mark", 0),
            append=arguments.get("append", False),
            unit=arguments.get("unit", "mm"),
        )
    
    elif name == "capture_viewport":
        result = sw_automation.capture_viewport(
            filepath=arguments.get("filepath"),
            view_name=arguments.get("view_name", "*Isometric"),
        )

    elif name == "show_interactive_studio":
        result = _show_interactive_studio_handler(arguments)

    # Assembly Tools (Phase 3)
    elif name == "add_component":
        result = sw_automation.add_component(
            component_path=arguments.get("component_path", ""),
            x=arguments.get("x", 0.0),
            y=arguments.get("y", 0.0),
            z=arguments.get("z", 0.0),
            unit=arguments.get("unit", "mm"),
        )

    elif name == "add_mate":
        result = sw_automation.add_mate(
            mate_type=arguments.get("mate_type", "coincident"),
            entity1=arguments.get("entity1", ""),
            entity2=arguments.get("entity2", ""),
            comp1=arguments.get("comp1"),
            comp2=arguments.get("comp2"),
            align=arguments.get("align", 0),
            flip=arguments.get("flip", False),
            distance=arguments.get("distance", 0.0),
            angle=arguments.get("angle", 0.0),
            unit=arguments.get("unit", "mm"),
        )

    elif name == "check_interference":
        result = sw_automation.check_interference(
            treat_coincidence_as_interference=arguments.get("treat_coincidence_as_interference", False),
        )

    elif name == "list_components":
        result = sw_automation.list_components()

    # Drawing Tools (Phase 3)
    elif name == "create_new_drawing":
        result = sw_automation.create_new_drawing(
            paper_size=arguments.get("paper_size", "A4"),
        )

    elif name == "create_drawing_from_model":
        result = sw_automation.create_drawing_from_model(
            model_path=arguments.get("model_path"),
            view_name=arguments.get("view_name", "*Isometric"),
            x=arguments.get("x", 0.1),
            y=arguments.get("y", 0.1),
            paper_size=arguments.get("paper_size", "A4"),
        )

    elif name == "export_drawing":
        result = sw_automation.export_drawing(
            filepath=arguments.get("filepath", ""),
            format=arguments.get("format", "pdf"),
        )

    elif name == "import_model_items":
        result = sw_automation.import_model_items(
            option=arguments.get("option", 1),
            types=arguments.get("types", 1),
            all_views=arguments.get("all_views", True),
            duplicate_dims=arguments.get("duplicate_dims", False),
            hidden_feature_dims=arguments.get("hidden_feature_dims", False),
            use_placement_in_sketch=arguments.get("use_placement_in_sketch", True),
        )

    elif name == "add_drawing_dimension":
        result = sw_automation.add_drawing_dimension(
            x=arguments.get("x", 0.0),
            y=arguments.get("y", 0.0),
            z=arguments.get("z", 0.0),
            unit=arguments.get("unit", "mm"),
        )

    elif name == "create_section_view":
        result = sw_automation.create_section_view(
            x=arguments.get("x", 0.0),
            y=arguments.get("y", 0.0),
            section_label=arguments.get("section_label", "A"),
            options=arguments.get("options", 32),
            line_x1=arguments.get("line_x1"),
            line_y1=arguments.get("line_y1"),
            line_x2=arguments.get("line_x2"),
            line_y2=arguments.get("line_y2"),
            parent_view=arguments.get("parent_view"),
            unit=arguments.get("unit", "mm"),
        )

    elif name == "insert_bom_table":
        result = sw_automation.insert_bom_table(
            template_path=arguments.get("template_path"),
            x=arguments.get("x", 0.0),
            y=arguments.get("y", 0.0),
            bom_type=arguments.get("bom_type", "top_level"),
            anchor_type=arguments.get("anchor_type", 1),
            configuration=arguments.get("configuration", ""),
            unit=arguments.get("unit", "mm"),
        )

    # Simulation Tools (Phase 4)
    elif name == "create_simulation_study":
        result = sw_automation.create_simulation_study(
            study_name=arguments.get("study_name", "StaticStudy"),
            study_type=arguments.get("study_type", "static"),
        )

    elif name == "set_study_material":
        result = sw_automation.set_study_material(
            material_name=arguments.get("material_name", "AISI 1020"),
            database_path=arguments.get("database_path"),
            config_name=arguments.get("config_name", "Default"),
        )

    elif name == "add_study_fixture":
        result = sw_automation.add_study_fixture(
            fixture_type=arguments.get("fixture_type", "fixed"),
            direction=arguments.get("direction"),
            face_point=arguments.get("face_point"),
            face_name=arguments.get("face_name"),
            unit=arguments.get("unit", "mm"),
            study_name=arguments.get("study_name"),
        )

    elif name == "add_study_load":
        result = sw_automation.add_study_load(
            load_type=arguments.get("load_type", "pressure"),
            value=arguments.get("value", 100000.0),
            unit=arguments.get("unit", "N/m^2"),
            direction=arguments.get("direction"),
            face_point=arguments.get("face_point"),
            face_name=arguments.get("face_name"),
            coord_unit=arguments.get("coord_unit", "mm"),
            study_name=arguments.get("study_name"),
        )

    elif name == "mesh_and_run_simulation":
        result = sw_automation.mesh_and_run_simulation(
            element_size_mm=arguments.get("element_size_mm", 2.0),
            tolerance_mm=arguments.get("tolerance_mm", 0.1),
            study_name=arguments.get("study_name"),
        )

    elif name == "get_simulation_results":
        result = sw_automation.get_simulation_results(
            study_name=arguments.get("study_name"),
        )

    # Knowledge & Operational Hardening Tools (Phase 5)
    elif name == "search_knowledge":
        km = get_knowledge_manager()
        recs = km.search(
            query=arguments.get("query"),
            tags=arguments.get("tags"),
            interface=arguments.get("interface"),
            min_confidence=arguments.get("min_confidence", 0.0),
        )
        result = {
            "success": True,
            "message": f"Found {len(recs)} matching knowledge records",
            "error_code": 0,
            "error_name": "swSuccess",
            "data": {
                "count": len(recs),
                "records": [r.to_dict() for r in recs],
            }
        }

    elif name == "record_knowledge":
        km = get_knowledge_manager()
        try:
            state_str = arguments.get("promotion_state", "discovered")
            rec = KnowledgeRecord(
                id=arguments.get("id", ""),
                title=arguments.get("title", ""),
                date=datetime.date.today().isoformat(),
                product_version="33.5.0.53 (SOLIDWORKS 2025)",
                api_interface=arguments.get("api_interface", ""),
                api_member=arguments.get("api_member", ""),
                problem=arguments.get("problem", ""),
                attempted_approaches=arguments.get("attempted_approaches", []),
                successful_approach=arguments.get("successful_approach", ""),
                source="live_operation",
                confidence=float(arguments.get("confidence", 0.8)),
                verification_method="manual_or_live_recording",
                promotion_state=PromotionState(state_str),
                tags=arguments.get("tags", []),
            )
            path = km.save_record(rec)
            result = {
                "success": True,
                "message": f"Recorded knowledge '{rec.id}' in tier '{rec.promotion_state.value}'",
                "error_code": 0,
                "error_name": "swSuccess",
                "data": {
                    "record": rec.to_dict(),
                    "path": path,
                }
            }
        except Exception as e:
            result = sw_automation._result(False, f"Failed to record knowledge: {e}", SwErrors.swInvalidInput)

    elif name == "create_checkpoint":
        doc, err = sw_automation.get_active_doc()
        if err:
            result = err
        else:
            cm = get_checkpoint_manager()
            res = cm.create_checkpoint(
                doc=doc,
                name=arguments.get("name", "checkpoint"),
                description=arguments.get("description", ""),
            )
            result = {
                "success": res.get("success", False),
                "message": res.get("message", res.get("error", "Failed to create checkpoint")),
                "error_code": 0 if res.get("success") else res.get("error_code", int(SwErrors.swUnknownError)),
                "error_name": "swSuccess" if res.get("success") else res.get("error_name", "swUnknownError"),
                "data": res.get("checkpoint"),
            }

    elif name == "rollback_checkpoint":
        doc, err = sw_automation.get_active_doc()
        if err:
            result = err
        else:
            cm = get_checkpoint_manager()
            res = cm.rollback_checkpoint(
                doc=doc,
                name=arguments.get("name", ""),
            )
            result = {
                "success": res.get("success", False),
                "message": res.get("message", res.get("error", "Failed to rollback checkpoint")),
                "error_code": 0 if res.get("success") else res.get("error_code", int(SwErrors.swUnknownError)),
                "error_name": "swSuccess" if res.get("success") else res.get("error_name", "swUnknownError"),
                "data": res,
            }

    # Diagnostics & Recovery Handlers
    elif name == "inspect_dialogs":
        result = sw_automation.inspect_dialogs()

    elif name == "dismiss_dialog":
        result = sw_automation.dismiss_dialog(
            hwnd=arguments.get("hwnd"),
            action=arguments.get("action", "OK"),
            force_close=arguments.get("force_close", False),
        )

    elif name == "load_addin":
        result = sw_automation.load_addin(
            name_or_path=arguments.get("name", ""),
        )

    elif name == "inspect_rebuild_errors":
        result = sw_automation.inspect_rebuild_errors()
    
    else:
        result = sw_automation._result(False, f"Unknown tool: {name}", SwErrors.swUnknownError)
    return result


def _dispatch_dialog_tool(name: str, arguments: dict) -> Dict[str, Any]:
    """Execute Win32-only dialog inspection and dismissal independently of STA worker queue."""
    if name == "inspect_dialogs":
        return sw_automation.inspect_dialogs()
    elif name == "dismiss_dialog":
        return sw_automation.dismiss_dialog(
            hwnd=arguments.get("hwnd"),
            action=arguments.get("action", "OK"),
            force_close=arguments.get("force_close", False),
        )
    return sw_automation._result(False, f"Unknown dialog tool: {name}", SwErrors.swUnknownError)


def _execute_tool_on_sta(name: str, arguments: dict) -> Tuple[Dict[str, Any], Optional[str], Optional[str], Optional[int]]:
    """Execute tool on STA worker thread and capture diagnostic metadata without crossing thread boundaries."""
    tid = threading.get_ident()
    dlg = find_blocking_modal_dialog()
    if dlg:
        title = dlg.get("title", "Modal Dialog")
        msgs = dlg.get("messages", [])
        details = f"{title}: {' '.join(msgs)}" if msgs else title
        raise SolidWorksModalBlockedException(
            f"SolidWorks is blocked by modal dialog: {details}",
            dlg,
            dispatched=False,
        )

    result = _dispatch_tool(name, arguments)
    sw_ver = None
    act_doc = None
    try:
        if sw_automation.is_connected and sw_automation._sw_app is not None:
            rev = sw_automation._sw_app.RevisionNumber
            sw_ver = str(rev() if callable(rev) else rev)
            doc = sw_automation._sw_app.ActiveDoc
            if doc is not None:
                title = doc.GetTitle
                act_doc = str(title() if callable(title) else title)
    except Exception:
        pass
    return result, sw_ver, act_doc, tid


def get_tool_timeout(tool_name: str, cfg: Any) -> float:
    """Get per-operation timeout based on configuration."""
    if tool_name in (
        "open_document",
        "save_document",
        "close_document",
        "create_new_part",
        "create_new_assembly",
        "create_new_drawing",
        "create_drawing_from_model",
        "connect_solidworks",
    ):
        return float(getattr(cfg, "open_save_timeout", 120.0))
    elif tool_name in ("export_drawing",):
        return float(getattr(cfg, "export_timeout", 180.0))
    elif tool_name in ("mesh_and_run_simulation",):
        return float(getattr(cfg, "simulation_timeout", 600.0))
    else:
        return float(getattr(cfg, "default_timeout", 60.0))


async def call_tool(
    ctx: ServerRequestContext,
    params: CallToolRequestParams,
) -> CallToolResult:
    """Handle MCP tool calls routed through the STA COM worker with watchdog and timeout."""
    try:
        name = params.name
        arguments = dict(params.arguments or {})
        validation_error = _validate_tool_arguments(name, arguments)
        if validation_error:
            err_env = error_envelope(
                message=validation_error,
                error_code=int(SwErrors.swInvalidInput),
                error_name="swInvalidInput",
                data={"validation_error": validation_error},
            )
            return err_env.to_call_tool_result()

        op_id = f"op_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        t_start = time.perf_counter()
        logger.info(f"Tool: {name}, Op: {op_id}, Args: {arguments}")

        cfg = get_config()

        if name in ("inspect_dialogs", "dismiss_dialog"):
            # Pure Win32 calls - execute in background thread (never blocked by STA queue)
            result = await asyncio.to_thread(_dispatch_dialog_tool, name, arguments)
            sw_ver = None
            act_doc = None
            tid = None
        elif name == "reset_sta_worker":
            # Administrative recovery tool - execute directly (never blocked by STA queue)
            from .core.com_worker import reset_sta_worker as do_reset_worker
            new_worker = do_reset_worker()
            # Invalidate stale COM pointers tied to the abandoned worker thread
            sw_automation._sw_app = None
            sw_automation._connected = False
            result = {
                "success": True,
                "message": "STA COM worker forcefully reset. Orphaned worker abandoned, fresh worker initialized.",
                "error_code": 0,
                "data": {
                    "new_thread_id": new_worker.thread_id,
                    "abandoned_call_cleared": True,
                }
            }
            sw_ver = None
            act_doc = None
            tid = new_worker.thread_id
        else:
            timeout = get_tool_timeout(name, cfg)
            watchdog_timeout = float(getattr(cfg, "watchdog_timeout", 4.0))
            sta = get_sta_worker()
            result, sw_ver, act_doc, tid = await sta.run_async(
                _execute_tool_on_sta,
                name,
                arguments,
                timeout=timeout,
                watchdog_timeout=watchdog_timeout,
                tool_name=name,
            )

        duration_ms = (time.perf_counter() - t_start) * 1000
        envelope = create_envelope(result, operation_id=op_id)
        envelope.diagnostics.duration_ms = duration_ms
        if sw_ver:
            envelope.diagnostics.sw_version = sw_ver
        if act_doc:
            envelope.diagnostics.active_document = act_doc
        if tid:
            envelope.diagnostics.thread_id = tid
        envelope.diagnostics.unit = sw_automation.units.default_unit

        logger.info(f"Result: success={envelope.success}, op={op_id}, duration={duration_ms:.2f}ms")
        if name == "show_interactive_studio" and envelope.success:
            return CallToolResult(
                content=[TextContent(type="text", text=result.get("message", ""))],
                structuredContent=result.get("data"),
                is_error=False,
                meta={
                    "ui": {
                        "resourceUri": "ui://solidworks/app.html"
                    },
                    "envelope": envelope.to_dict()
                }
            )
        return envelope.to_call_tool_result()

    except SolidWorksModalBlockedException as e:
        duration_ms = (time.perf_counter() - t_start) * 1000 if "t_start" in locals() else 0.0
        current_op = op_id if "op_id" in locals() else f"op_err_{uuid.uuid4().hex[:6]}"
        logger.warning(f"Tool '{name}' blocked by modal dialog: {e}")
        dlg = getattr(e, "dialog_info", {})
        dispatched = getattr(e, "dispatched", True)
        age_s = round(duration_ms / 1000.0, 2)
        if dispatched:
            msg = f"{e}. Warning: tool '{name}' was started on the server and may still execute once unblocked. Inspect model/document state before retrying."
        else:
            msg = f"{e}. Tool '{name}' was NOT dispatched to SolidWorks and will not execute."
        err_env = error_envelope(
            message=msg,
            error_code=int(SwErrors.swModalDialogBlocking),
            error_name="swModalDialogBlocking",
            data={
                "dialog": dlg,
                "blocked_by_dialog": True,
                "may_still_execute": dispatched,
                "dispatched": dispatched,
                "tool": name,
                "age_s": age_s,
            },
            details=f"blocked_by_dialog=True, may_still_execute={dispatched}",
            duration_ms=duration_ms,
            operation_id=current_op,
        )
        return err_env.to_call_tool_result()

    except TimeoutError as e:
        duration_ms = (time.perf_counter() - t_start) * 1000 if "t_start" in locals() else 0.0
        current_op = op_id if "op_id" in locals() else f"op_err_{uuid.uuid4().hex[:6]}"
        logger.error(f"Tool '{name}' timed out: {e}")
        age_s = round(duration_ms / 1000.0, 2)
        err_env = error_envelope(
            message=f"{e}. Warning: tool '{name}' was started on the server and may still execute in the background. Inspect model/document state before retrying.",
            error_code=int(SwErrors.swOperationTimeout),
            error_name="swOperationTimeout",
            details={
                "tool": name,
                "timeout_seconds": timeout if "timeout" in locals() else 60.0,
                "may_still_execute": True,
                "age_s": age_s,
            },
            duration_ms=duration_ms,
            operation_id=current_op,
        )
        return err_env.to_call_tool_result()

    except AbandonedCallRunningException as e:
        duration_ms = (time.perf_counter() - t_start) * 1000 if "t_start" in locals() else 0.0
        current_op = op_id if "op_id" in locals() else f"op_err_{uuid.uuid4().hex[:6]}"
        logger.error(f"Worker busy with abandoned call: {e}")
        abandoned_tool = getattr(e, "abandoned_tool", "")
        age_s = round(getattr(e, "age", 0.0), 2)
        err_env = error_envelope(
            message=f"{e}. The earlier call may still be running; inspect model/document state before retrying.",
            error_code=int(SwErrors.swWorkerBusy),
            error_name="swWorkerBusy",
            details={
                "abandoned_tool": abandoned_tool,
                "tool": abandoned_tool,
                "age_s": age_s,
                "may_still_execute": True,
            },
            duration_ms=duration_ms,
            operation_id=current_op,
        )
        return err_env.to_call_tool_result()


    except Exception as e:
        duration_ms = (time.perf_counter() - t_start) * 1000 if "t_start" in locals() else 0.0
        current_op = op_id if "op_id" in locals() else f"op_err_{uuid.uuid4().hex[:6]}"
        logger.error(f"Tool error: {e}\n{traceback.format_exc()}")
        err_env = error_envelope(
            message=f"Tool error: {e}",
            error_code=int(SwErrors.swGeneralError),
            error_name="swGeneralError",
            details=traceback.format_exc(),
            duration_ms=duration_ms,
            operation_id=current_op,
        )
        return err_env.to_call_tool_result()


async def list_resources(
    params: Optional[PaginatedRequestParams] = None,
    context: Optional[ServerRequestContext] = None,
) -> ListResourcesResult:
    """List resources exposed by SolidWorks MCP (MCP Apps interactive studio UI)."""
    return ListResourcesResult(
        resources=[
            Resource(
                uri="ui://solidworks/app.html",
                name="SolidWorks Interactive Studio",
                description="Interactive CAD studio with live parametric sliders, viewport preview, and SolidWorks rebuilds.",
                mimeType="text/html;profile=mcp-app",
            )
        ]
    )


async def read_resource(
    params: ReadResourceRequestParams,
    context: Optional[ServerRequestContext] = None,
) -> ReadResourceResult:
    """Read resource content (serves the studio HTML for MCP Apps clients)."""
    uri_str = str(getattr(params, "uri", ""))
    if uri_str == "ui://solidworks/app.html":
        from .ui.bridge import _current_app_html, get_bridge_port
        port = get_bridge_port() or 54321
        return ReadResourceResult(
            contents=[
                TextResourceContents(
                    uri="ui://solidworks/app.html",
                    mimeType="text/html;profile=mcp-app",
                    meta={
                        "ui": {
                            "csp": {
                                "connectDomains": [
                                    f"http://127.0.0.1:{port}",
                                    "http://127.0.0.1:*",
                                    "http://localhost:*",
                                    "data:",
                                    "blob:",
                                ],
                                "resourceDomains": ["data:", "blob:"],
                            }
                        }
                    },
                    text=_current_app_html,
                )
            ]
        )
    raise ValueError(f"Resource not found: {uri_str}")


server = Server(
    "solidworks-mcp-server",
    on_list_tools=list_tools,
    on_call_tool=call_tool,
    on_list_resources=list_resources,
    on_read_resource=read_resource,
)


# ============================================================================
# FIXED: Execute Python with stdout capture
# ============================================================================

_session_exec_unlocked: bool = False


def _execute_python_fixed(code: str, user_confirmed_risks: bool = False) -> Dict:
    """
    Execute custom Python code with access to SolidWorks.
    NOTE: Guarded by interactive consent gate and config flag 'enable_execute_python' (default: False).
    """
    global _session_exec_unlocked
    cfg = get_config()

    # Check if unlocked via config, env var, prior session approval, or explicit argument
    is_allowed = (
        cfg.enable_execute_python
        or os.environ.get("SW_MCP_ALLOW_EXEC", "").strip().lower() in ("1", "true", "yes", "on")
        or os.environ.get("SWMCP_ENABLE_EXECUTE_PYTHON", "").strip().lower() in ("1", "true", "yes", "on")
        or _session_exec_unlocked
        or user_confirmed_risks
    )

    if not is_allowed:
        logger.warning("execute_python rejected: interactive consent required")
        consent_prompt = (
            "PERMISSION REQUIRED: `execute_python` is locked by default for security.\n\n"
            "AI Agent Directive:\n"
            "You MUST ask the human user for explicit permission in chat before executing arbitrary Python code.\n\n"
            "Explain the following to the user:\n"
            "- BENEFITS: Grants direct access to the full SolidWorks COM API (`sw_app`, `model`, `SelectionMgr`) "
            "for custom macros, niche COM operations not covered by the 65 standard MCP tools, and complex batch geometry math.\n"
            "- RISKS: Executes arbitrary Python code with the current Windows user's privileges on the host system, "
            "bypassing standard MCP tool parameter guards and filesystem sandbox allowlists.\n\n"
            "Once the user gives explicit consent in chat, re-invoke `execute_python` with `user_confirmed_risks=true` "
            "or set environment variable `SW_MCP_ALLOW_EXEC=1`."
        )
        return {
            "success": False,
            "message": consent_prompt,
            "error_code": int(SwErrors.swInvalidCall),
            "error_name": "swInvalidCall",
            "security_gate": "consent_required",
            "timestamp": datetime.datetime.now().isoformat(),
        }

    # Unlock session if explicit user confirmation was provided
    if user_confirmed_risks:
        _session_exec_unlocked = True

    try:
        if not sw_automation.is_connected:
            if not getattr(cfg, "auto_connect", True):
                return {
                    "success": False,
                    "message": "Not connected to SolidWorks. Call connect_solidworks first.",
                    "error_code": int(SwErrors.swConnectionError),
                    "error_name": "swConnectionError",
                    "timestamp": datetime.datetime.now().isoformat(),
                }
            r = sw_automation.connect()
            if not r["success"]:
                return r
        
        import win32com.client
        import pythoncom
        import math
        import os as os_module
        
        # Prepare execution context with many useful objects
        exec_globals = {
            # SolidWorks objects
            'sw': sw_automation.app,
            'doc': sw_automation.app.ActiveDoc if sw_automation.app else None,
            'automation': sw_automation,
            
            # COM libraries
            'win32com': win32com,
            'pythoncom': pythoncom,
            
            # Standard libraries
            'math': math,
            'os': os_module,
            'json': json,
            
            # Result placeholder
            'result': None,
        }
        
        # Capture stdout and stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        
        try:
            sys.stdout = captured_stdout
            sys.stderr = captured_stderr
            
            # Execute the code
            exec(code, exec_globals)
            
        finally:
            # Always restore stdout/stderr
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        
        # Get captured output
        stdout_text = captured_stdout.getvalue()
        stderr_text = captured_stderr.getvalue()
        result_val = exec_globals.get('result')
        
        # Build response message
        message_parts = []
        
        if stdout_text:
            message_parts.append(f"=== Output ===\n{stdout_text.rstrip()}")
        
        if stderr_text:
            message_parts.append(f"=== Stderr ===\n{stderr_text.rstrip()}")
        
        if result_val is not None:
            message_parts.append(f"=== Result ===\n{result_val}")
        
        if not message_parts:
            message_parts.append("Code executed successfully (no output)")
        
        return {
            "success": True,
            "message": "\n\n".join(message_parts),
            "error_code": 0,
            "error_name": "swSuccess",
            "timestamp": datetime.datetime.now().isoformat(),
            "data": {
                "stdout": stdout_text,
                "stderr": stderr_text,
                "result": str(result_val) if result_val is not None else None
            }
        }
        
    except SyntaxError as e:
        return {
            "success": False,
            "message": f"Syntax error: {e}",
            "error_code": int(SwErrors.swInvalidInput),
            "error_name": "swInvalidInput",
            "timestamp": datetime.datetime.now().isoformat(),
            "data": {"error_type": "SyntaxError", "details": str(e)}
        }
        
    except Exception as e:
        tb = traceback.format_exc()
        return {
            "success": False,
            "message": f"Execution error: {e}\n\nTraceback:\n{tb}",
            "error_code": int(SwErrors.swGeneralError),
            "error_name": "swGeneralError",
            "timestamp": datetime.datetime.now().isoformat(),
            "data": {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "traceback": tb
            }
        }


# ============================================================================
# FIXED: list_features
# ============================================================================

def _list_features_fixed() -> Dict:
    """
    List all features in the active document.
    FIXED v4.1: Property access for SW 2025 (FirstFeature, GetNextFeature, GetTypeName2).
    """
    try:
        doc, err = sw_automation.get_active_doc()
        if err:
            return err
        
        features = []
        
        # FIXED: FirstFeature is a property in SW 2025 COM, not a method
        try:
            feat = doc.FirstFeature
        except AttributeError:
            feat = doc.FirstFeature()
        
        while feat is not None:
            try:
                name = ""
                feat_type = ""
                suppressed = False
                
                try:
                    name = feat.Name
                except:
                    name = "<unknown>"
                
                # FIXED: GetTypeName2 is a property in SW 2025 COM
                try:
                    feat_type = feat.GetTypeName2
                    if callable(feat_type):
                        feat_type = feat_type()
                except:
                    try:
                        feat_type = feat.GetTypeName()
                    except:
                        feat_type = "<unknown>"
                
                try:
                    suppressed = feat.IsSuppressed()
                except:
                    suppressed = False
                
                features.append({
                    "name": name,
                    "type": feat_type,
                    "suppressed": bool(suppressed)
                })

                # Iterate subfeatures (absorbed sketches, reference geometry, etc.)
                try:
                    subfeat = getattr(feat, "GetFirstSubFeature", None)
                    if callable(subfeat):
                        subfeat = subfeat()
                    while subfeat is not None:
                        try:
                            s_name = getattr(subfeat, "Name", "<unknown>")
                            s_type = getattr(subfeat, "GetTypeName2", None)
                            if callable(s_type):
                                s_type = s_type()
                            elif s_type is None:
                                s_type = getattr(subfeat, "GetTypeName", lambda: "<unknown>")()
                            s_supp = False
                            try:
                                s_supp = subfeat.IsSuppressed()
                            except Exception:
                                pass
                            features.append({
                                "name": s_name,
                                "type": str(s_type),
                                "suppressed": bool(s_supp),
                                "parent": name,
                            })
                        except Exception:
                            pass
                        next_sub = getattr(subfeat, "GetNextSubFeature", None)
                        subfeat = next_sub() if callable(next_sub) else next_sub
                except Exception:
                    pass
                
            except Exception as e:
                features.append({
                    "name": "<error>",
                    "type": str(e),
                    "suppressed": False
                })
            
            # FIXED: GetNextFeature is a property in SW 2025 COM
            try:
                feat = feat.GetNextFeature
            except:
                break
        
        return {
            "success": True,
            "message": f"{len(features)} features found",
            "error_code": 0,
            "error_name": "swSuccess",
            "data": {"features": features, "count": len(features)}
        }
        
    except Exception as e:
        logger.error(f"List features error: {e}\n{traceback.format_exc()}")
        return {
            "success": False,
            "message": f"Error: {e}",
            "error_code": int(SwErrors.swGeneralError),
            "error_name": "swGeneralError",
            "data": {}
        }

# ============================================================================
# NEW: close_sketch handler
# ============================================================================

def _close_sketch_handler() -> Dict:
    """
    Close the active sketch if one is open.
    Returns sketch status info.
    """
    try:
        doc, err = sw_automation.get_active_doc()
        if err:
            return err

        dtype = sw_automation.get_document_type(doc)
        if dtype == "Drawing":
            return {
                "success": False,
                "message": "close_sketch is not supported for Drawing documents",
                "error_code": int(SwErrors.swInvalidDocumentType),
                "error_name": "swInvalidDocumentType",
                "data": {"document_type": dtype}
            }
        
        # Check if sketch is active
        had_active = False
        try:
            act = get_attr_or_call(doc, "GetActiveSketch2")
            if act is not None:
                had_active = True
            else:
                active_sketch = getattr(doc.SketchManager, "ActiveSketch", None)
                if active_sketch is not None and getattr(active_sketch, "IsSketchEditable", False):
                    had_active = True
        except:
            pass
        
        if had_active:
            try:
                doc.InsertSketch2(True)
            except:
                try:
                    doc.SketchManager.InsertSketch(True)
                except:
                    pass
            
            # Post-condition check: verify sketch mode is actually closed
            still_active = False
            try:
                act = get_attr_or_call(doc, "GetActiveSketch2")
                if act is not None:
                    still_active = True
                else:
                    active_sketch = getattr(doc.SketchManager, "ActiveSketch", None)
                    if active_sketch is not None and getattr(active_sketch, "IsSketchEditable", False):
                        still_active = True
            except:
                pass

            if still_active:
                return {
                    "success": False,
                    "message": "Post-condition failed: Sketch is still active after close_sketch",
                    "error_code": int(SwErrors.swSketchError),
                    "error_name": "swSketchError",
                    "data": {"had_active_sketch": True, "action": "failed"}
                }

            return {
                "success": True,
                "message": "Sketch closed successfully",
                "error_code": 0,
                "error_name": "swSuccess",
                "data": {"had_active_sketch": True, "action": "closed"}
            }
        else:
            return {
                "success": False,
                "message": "no active sketch",
                "error_code": int(SwErrors.swInvalidCall),
                "error_name": "swInvalidCall",
                "data": {"had_active_sketch": False, "action": "none"}
            }
        
    except Exception as e:
        logger.error(f"Close sketch error: {e}\n{traceback.format_exc()}")
        return {
            "success": False,
            "message": f"Error: {e}",
            "error_code": int(SwErrors.swGeneralError),
            "error_name": "swGeneralError",
            "data": {"traceback": traceback.format_exc()}
        }


# ============================================================================
# NEW: get_sketch_status handler
# ============================================================================

def _get_sketch_status_handler() -> Dict:
    """
    Get diagnostic info about the current sketch state.
    Useful for debugging sketch/extrude issues.
    """
    try:
        doc, err = sw_automation.get_active_doc()
        if err:
            return err
        
        info = {
            "has_active_sketch": False,
            "active_sketch_name": None,
            "sketch_count": 0,
            "sketch_names": [],
            "feature_count": 0,
            "extrusion_count": 0,
        }
        
        # Check active sketch (ignore drawing sheet background sketch)
        dtype = sw_automation.get_document_type(doc)
        if dtype != "Drawing":
            try:
                act = get_attr_or_call(doc, "GetActiveSketch2")
                active_sketch = getattr(doc.SketchManager, "ActiveSketch", None)
                is_editable = getattr(active_sketch, "IsSketchEditable", False) if active_sketch else False
                if act is not None or is_editable:
                    info["has_active_sketch"] = True
                    target = act or active_sketch
                    try:
                        info["active_sketch_name"] = getattr(target, "Name", "<unknown>")
                    except:
                        info["active_sketch_name"] = "<unknown>"
            except:
                pass
        
        # Walk feature tree (using PROPERTIES not methods)
        try:
            feat = doc.FirstFeature
            while feat is not None:
                try:
                    feat_type = get_attr_or_call(feat, "GetTypeName2")
                    info["feature_count"] += 1
                    
                    if feat_type == "ProfileFeature":
                        info["sketch_count"] += 1
                        info["sketch_names"].append(feat.Name)
                    elif feat_type == "Extrusion":
                        info["extrusion_count"] += 1
                except:
                    pass
                try:
                    feat = feat.GetNextFeature
                except:
                    break
        except:
            pass
        
        # Build readable message
        status = "OPEN" if info["has_active_sketch"] else "CLOSED"
        msg = (f"Sketch status: {status}. "
               f"Sketches: {info['sketch_count']} {info['sketch_names']}. "
               f"Extrusions: {info['extrusion_count']}. "
               f"Total features: {info['feature_count']}")
        
        return {
            "success": True,
            "message": msg,
            "error_code": 0,
            "error_name": "swSuccess",
            "data": info
        }
        
    except Exception as e:
        logger.error(f"Sketch status error: {e}\n{traceback.format_exc()}")
        return {
            "success": False,
            "message": f"Error: {e}",
            "error_code": int(SwErrors.swGeneralError),
            "error_name": "swGeneralError",
            "data": {"traceback": traceback.format_exc()}
        }


# ============================================================================
# Main Entry Point
# ============================================================================


# ============================================================================
# Interactive Studio Handler (Fast-Bridge & UI Generator)
# ============================================================================

def _show_interactive_studio_handler(arguments: dict) -> Dict[str, Any]:
    """
    Launch Fast-Bridge daemon, inspect model dimensions, capture viewport snapshot,
    generate interactive studio HTML artifact, and return embedding directives.
    """
    try:
        from .ui.bridge import start_bridge, get_bridge_port
        from .ui.generator import generate_studio_html
        
        # 1. Ensure Fast-Bridge is running
        port = get_bridge_port()
        if not port:
            try:
                port = start_bridge()
            except Exception as e:
                logger.warning(f"Could not start fast bridge: {e}")
                port = 54321

        # 2. Get active document
        doc, err = sw_automation.get_active_doc()
        if err:
            return err
        
        model_title = "SolidWorks Model"
        if doc:
            try:
                title_getter = getattr(doc, "GetTitle", lambda: getattr(doc, "Title", "SolidWorks Model"))
                model_title = title_getter() if callable(title_getter) else str(title_getter)
            except Exception:
                pass

        # 3. Query dimensions
        feat_filter = arguments.get("feature_filter")
        dims_res = sw_automation.get_dimensions(feature_name=feat_filter)
        dims_data = dims_res.get("data") if isinstance(dims_res, dict) and dims_res.get("success") else None
        if isinstance(dims_data, list):
            dims = dims_data
        elif isinstance(dims_data, dict):
            dims = dims_data.get("dimensions", [])
        elif isinstance(dims_res, dict) and isinstance(dims_res.get("dimensions"), list):
            dims = dims_res.get("dimensions", [])
        else:
            dims = []

        # 4. Viewport snapshot
        view_name = arguments.get("view_name", "*Isometric")
        cap_res = sw_automation.capture_viewport(view_name=view_name)
        img_b64 = ""
        if isinstance(cap_res, dict) and cap_res.get("success"):
            fp = cap_res.get("data", {}).get("filepath")
            if fp and os.path.exists(fp):
                try:
                    with open(fp, "rb") as f:
                        img_b64 = f"data:image/png;base64,{base64.b64encode(f.read()).decode('ascii')}"
                except Exception:
                    pass

        # 5. Model bounds
        insp_res = sw_automation.inspect_model()
        bbox_info = insp_res.get("data", {}) if isinstance(insp_res, dict) and insp_res.get("success") else None

        # 6. Artifact destination
        if arguments.get("output_path"):
            artifact_path = Path(arguments["output_path"])
        elif arguments.get("output_dir"):
            artifact_path = Path(arguments["output_dir"]) / "solidworks_studio.html"
        elif os.environ.get("SOLIDWORKS_STUDIO_TEST_DIR"):
            artifact_path = Path(os.environ["SOLIDWORKS_STUDIO_TEST_DIR"]) / "solidworks_studio.html"
        else:
            user_profile = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
            brain_base = user_profile / ".gemini" / "antigravity" / "brain"
            if brain_base.exists():
                brain_dirs = [p for p in brain_base.iterdir() if p.is_dir() and not p.name.startswith(".")]
                if brain_dirs:
                    brain_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    artifact_dir = brain_dirs[0]
                else:
                    artifact_dir = Path.cwd() / "artifacts"
            else:
                artifact_dir = Path.cwd() / "artifacts"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_dir / "solidworks_studio.html"

        artifact_path.parent.mkdir(parents=True, exist_ok=True)

        raw_unit = getattr(sw_automation._units, "default_unit", "mm")
        if hasattr(raw_unit, "value") and isinstance(raw_unit.value, str):
            unit_label = raw_unit.value
        elif isinstance(raw_unit, str):
            unit_label = raw_unit
        else:
            unit_label = "mm"
        if "mock" in unit_label.lower():
            unit_label = "mm"
        if "mock" in str(model_title).lower():
            model_title = "SolidWorks Model" 

        feature_labels = arguments.get("feature_labels") or {}
        dimension_labels = arguments.get("dimension_labels") or {}

        generate_studio_html(
            model_title=model_title,
            dimensions=dims,
            units=unit_label,
            initial_image_base64=img_b64,
            bbox=bbox_info.get("dimensions_mm") if bbox_info else None,
            bridge_port=port,
            feature_labels=feature_labels,
            dimension_labels=dimension_labels,
            output_path=str(artifact_path),
        )

        # 7. Formatted message with embed tag & summary table
        clean_path = str(artifact_path).replace("\\", "/")
        artifact_file_url = f"file:///{clean_path}"
        
        table_lines = ["| Dimension | Parameter / Purpose | Value | Feature |", "| :--- | :--- | :--- | :--- |"]
        for d in dims[:10]:
            d_full = d.get('full_name', '')
            d_hint = dimension_labels.get(d_full) or d.get('semantic_hint', d.get('name', ''))
            f_owner = d.get('owner_feature', '')
            f_display = feature_labels.get(f_owner, f_owner)
            table_lines.append(f"| `{d_full}` | **{d_hint}** | `{d.get('value')} {d.get('units')}` | {f_display} |")
        if len(dims) > 10:
            table_lines.append(f"| *...and {len(dims) - 10} more* | | |")
        
        dim_summary = "\n".join(table_lines) if dims else "*No driving dimensions detected.*"

        message = (
            f"### 🎛️ SolidWorks Interactive Studio\n\n"
            f"<agent-embed src=\"{artifact_file_url}\"></agent-embed>\n\n"
            f"**Model:** `{model_title}` • **Fast-Bridge:** `http://127.0.0.1:{port}` • **Dimensions:** {len(dims)}\n\n"
            f"{dim_summary}\n\n"
            f"> *Drag sliders in the interactive studio to update SolidWorks in real-time. Use 'Prompt Agent' when done.*"
        )

        return {
            "success": True,
            "message": message,
            "error_code": 0,
            "error_name": "swSuccess",
            "data": {
                "modelTitle": model_title,
                "bridgePort": port,
                "dimensionsCount": len(dims),
                "artifactPath": str(artifact_path),
                "artifactUrl": artifact_file_url,
                "bridgeUrl": f"http://127.0.0.1:{port}/api/app.html",
                "dimensions": dims,
                "_meta": {
                    "ui": {
                        "resourceUri": "ui://solidworks/app.html"
                    }
                }
            }
        }
    except Exception as e:
        logger.error(f"Error in show_interactive_studio: {e}\n{traceback.format_exc()}")
        return {
            "success": False,
            "message": f"Failed to launch interactive studio: {e}",
            "error_code": int(SwErrors.swGeneralError),
            "error_name": "swGeneralError",
            "data": {"traceback": traceback.format_exc()}
        }


async def main():
    """Main entry point for MCP server"""
    # 0. Check and apply updates silently before initializing stdio loop
    try:
        from solidworks_mcp.utils.auto_updater import check_and_apply_updates
        check_and_apply_updates()
    except Exception as update_err:
        sys.stderr.write(f"[solidworks_mcp] Auto-updater warning: {update_err}\n")

    logger.info("Starting SolidWorks MCP Server v4.0.0 (Fixed)...")
    logger.info(f"Log file: {LOG_FILE}")
    
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run():
    """Run the server"""
    import asyncio
    asyncio.run(main())


if __name__ == "__main__":
    run()
