"""
SolidWorks Sketch Operations
----------------------------
Create sketches and draw 2D geometry.
"""

import math
import logging
import traceback
from typing import Optional, Dict, List, Tuple

import win32com.client
import pythoncom

from ..constants import SwErrors, SwPlanes

logger = logging.getLogger(__name__)


class SketchOperations:
    """
    Mixin class for sketch operations
    
    Requires parent class to have:
    - get_active_doc(): Document access method
    - _result(): Result factory method
    - _units: UnitConverter instance
    """
    
    def create_sketch(self, plane: str = "Front") -> Dict:
        """
        Create a new sketch on specified plane
        
        Args:
            plane: Plane name - "Front", "Top", or "Right"
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            
            plane_map = {
                "front": "Front Plane",
                "top": "Top Plane",
                "right": "Right Plane",
                "front plane": "Front Plane",
                "top plane": "Top Plane",
                "right plane": "Right Plane",
            }
            plane_key = (plane or "").strip().lower()
            if plane_key not in plane_map:
                return self._result(
                    False,
                    f"Invalid plane '{plane}'. Valid planes are: Front, Top, Right",
                    SwErrors.swInvalidParameter,
                )
            plane_name = plane_map[plane_key]
            
            # Create empty variant for callout parameter
            empty_callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
            
            # Select plane
            result = doc.Extension.SelectByID2(
                plane_name, "PLANE", 0, 0, 0, False, 0, empty_callout, 0
            )
            
            if not result:
                return self._result(False, f"Could not select {plane_name}",
                                  SwErrors.swSelectionError)
            
            # Insert sketch
            doc.InsertSketch2(True)
            
            # Post-condition check: verify active sketch mode was entered
            is_active = False
            try:
                sk_mgr = getattr(doc, "SketchManager", None)
                if sk_mgr is not None and getattr(sk_mgr, "ActiveSketch", None) is not None:
                    is_active = True
            except Exception:
                pass
            if not is_active:
                try:
                    from ..core.com_compat import get_attr_or_call
                    if get_attr_or_call(doc, "GetSketchMode"):
                        is_active = True
                except Exception:
                    pass
            
            if not is_active:
                return self._result(
                    False,
                    f"Post-condition failed: Active sketch mode was not entered on {plane_name}",
                    SwErrors.swSketchError,
                )
            
            return self._result(True, f"Sketch created on {plane_name}",
                              SwErrors.swSuccess, {"plane": plane_name, "sketch_active": True})
            
        except Exception as e:
            logger.error(f"Create sketch error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
    
    def create_sketch_on_face(self, x: float = 0, y: float = 0, z: float = 0,
                              unit: str = None) -> Dict:
        """
        Create a new sketch on a face selected by coordinate.
        ADDED v4.1: Enables cut-extrude on existing body faces (not just ref planes).
        
        Args:
            x, y, z: Coordinates (in user units) of a point ON the target face
            unit: Unit for coordinates (uses default if None)
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            
            # Convert to meters
            x_m = self._units.to_meters(x, unit)
            y_m = self._units.to_meters(y, unit)
            z_m = self._units.to_meters(z, unit)
            unit_str = unit or self._units.default_unit.value
            
            # Clear any selection
            doc.ClearSelection2(True)
            
            # Select face at the given coordinates
            empty_callout = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
            selected = doc.Extension.SelectByID2(
                "", "FACE", x_m, y_m, z_m, False, 0, empty_callout, 0
            )
            
            if not selected:
                return self._result(False,
                    f"No face found at ({x}{unit_str}, {y}{unit_str}, {z}{unit_str}). "
                    f"Coordinates must be on an existing body face.",
                    SwErrors.swSelectionError)
            
            # Insert sketch on the selected face
            doc.InsertSketch2(True)

            # Post-condition check: verify active sketch mode was entered
            is_active = False
            try:
                sk_mgr = getattr(doc, "SketchManager", None)
                if sk_mgr is not None and getattr(sk_mgr, "ActiveSketch", None) is not None:
                    is_active = True
            except Exception:
                pass
            if not is_active:
                try:
                    from ..core.com_compat import get_attr_or_call
                    if get_attr_or_call(doc, "GetSketchMode"):
                        is_active = True
                except Exception:
                    pass

            if not is_active:
                return self._result(
                    False,
                    f"Post-condition failed: Active sketch mode was not entered on selected face",
                    SwErrors.swSketchError,
                )
            
            return self._result(True,
                f"Sketch created on face at ({x}, {y}, {z}) {unit_str}",
                SwErrors.swSuccess,
                {"face_point": {"x": x, "y": y, "z": z}, "unit": unit_str, "sketch_active": True})
            
        except Exception as e:
            logger.error(f"Create sketch on face error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
    
    def exit_sketch(self) -> Dict:
        """
        Exit the current sketch
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            
            doc.InsertSketch2(True)

            # Post-condition check: verify sketch mode exited
            still_active = False
            try:
                sk_mgr = getattr(doc, "SketchManager", None)
                if sk_mgr is not None:
                    act_sk = getattr(sk_mgr, "ActiveSketch", None)
                    if act_sk is not None and getattr(act_sk, "IsSketchEditable", False):
                        still_active = True
            except Exception:
                pass

            if still_active:
                return self._result(
                    False,
                    "Post-condition failed: Sketch mode is still active after exit_sketch",
                    SwErrors.swSketchError,
                )
            
            return self._result(True, "Exited sketch", SwErrors.swSuccess, {"sketch_active": False})
            
        except Exception as e:
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)

    def _require_active_sketch_in_3d(self, doc) -> Optional[Dict]:
        """In Part and Assembly documents, require an active sketch to avoid auto-creating orphan 3D sketches."""
        dtype = self.get_document_type(doc)
        if dtype in ("Part", "Assembly"):
            sk_mgr = getattr(doc, "SketchManager", None)
            if sk_mgr is None or getattr(sk_mgr, "ActiveSketch", None) is None:
                return self._result(False, "Failed - ensure sketch is active", SwErrors.swSketchError)
        return None
    
    def draw_line(self, x1: float = 0, y1: float = 0, 
                  x2: float = 100, y2: float = 0, unit: str = None,
                  construction: bool = False) -> Dict:
        """
        Draw a line in the active sketch
        
        Args:
            x1, y1: Start point coordinates
            x2, y2: End point coordinates
            unit: Unit for coordinates (uses default if None)
            construction: If True, set line as construction geometry (centerline)
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err
            
            # Reject zero-length line
            if abs(x1 - x2) < 1e-9 and abs(y1 - y2) < 1e-9:
                return self._result(
                    False,
                    f"Zero-length line rejected: start ({x1}, {y1}) and end ({x2}, {y2}) are identical.",
                    SwErrors.swInvalidParameter,
                )

            # Convert to meters
            x1_m = self._units.to_meters(x1, unit)
            y1_m = self._units.to_meters(y1, unit)
            x2_m = self._units.to_meters(x2, unit)
            y2_m = self._units.to_meters(y2, unit)
            
            # Draw line
            line = doc.SketchManager.CreateLine(x1_m, y1_m, 0, x2_m, y2_m, 0)
            
            if line is None:
                return self._result(False, "Failed - ensure sketch is active",
                                  SwErrors.swSketchError)
            
            if construction:
                try:
                    line.ConstructionGeometry = True
                except Exception:
                    pass
            
            # Calculate length
            length_m = math.sqrt((x2_m - x1_m)**2 + (y2_m - y1_m)**2)
            length_display = self._units.from_meters(length_m, unit)
            unit_str = unit or self._units.default_unit.value
            
            return self._result(True, f"Line: {length_display:.2f}{unit_str}",
                              SwErrors.swSuccess,
                              {"length": length_display, "unit": unit_str})
            
        except Exception as e:
            logger.error(f"Draw line error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
    
    def draw_circle(self, x: float = 0, y: float = 0, 
                    radius: float = 25, unit: str = None) -> Dict:
        """
        Draw a circle in the active sketch
        
        Args:
            x, y: Center point coordinates
            radius: Circle radius
            unit: Unit for dimensions (uses default if None)
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err
            
            if radius <= 0:
                return self._result(
                    False,
                    f"Invalid circle radius {radius}: radius must be greater than 0.",
                    SwErrors.swInvalidParameter,
                )

            # Convert to meters
            x_m = self._units.to_meters(x, unit)
            y_m = self._units.to_meters(y, unit)
            radius_m = self._units.to_meters(radius, unit)
            
            # Draw circle (center point and edge point)
            circle = doc.SketchManager.CreateCircle(
                x_m, y_m, 0,              # Center
                x_m + radius_m, y_m, 0    # Edge point
            )
            
            if circle is None:
                return self._result(False, "Failed - ensure sketch is active",
                                  SwErrors.swSketchError)
            
            unit_str = unit or self._units.default_unit.value
            
            return self._result(True, f"Circle: r={radius}{unit_str}",
                              SwErrors.swSuccess,
                              {"radius": radius, "unit": unit_str,
                               "center_x": x, "center_y": y})
            
        except Exception as e:
            logger.error(f"Draw circle error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
    
    def draw_rectangle(self, x1: float = -50, y1: float = -25,
                       x2: float = 50, y2: float = 25, unit: str = None) -> Dict:
        """
        Draw a rectangle in the active sketch
        
        Args:
            x1, y1: First corner coordinates
            x2, y2: Opposite corner coordinates
            unit: Unit for dimensions
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err
            
            if abs(x1 - x2) < 1e-9 or abs(y1 - y2) < 1e-9:
                return self._result(
                    False,
                    "Zero-area rectangle rejected: width and height must be greater than 0.",
                    SwErrors.swInvalidParameter,
                )

            # Convert to meters
            x1_m = self._units.to_meters(x1, unit)
            y1_m = self._units.to_meters(y1, unit)
            x2_m = self._units.to_meters(x2, unit)
            y2_m = self._units.to_meters(y2, unit)
            
            # Draw rectangle
            rect = doc.SketchManager.CreateCornerRectangle(
                x1_m, y1_m, 0, x2_m, y2_m, 0
            )
            
            if rect is None:
                return self._result(False, "Failed - ensure sketch is active",
                                  SwErrors.swSketchError)
            
            width = abs(x2 - x1)
            height = abs(y2 - y1)
            unit_str = unit or self._units.default_unit.value
            
            return self._result(True, f"Rectangle: {width}x{height}{unit_str}",
                              SwErrors.swSuccess,
                              {"width": width, "height": height, "unit": unit_str})
            
        except Exception as e:
            logger.error(f"Draw rectangle error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
    
    def draw_arc_center(self, cx: float = 0, cy: float = 0, radius: float = 25,
                        start_angle: float = 0, end_angle: float = 90,
                        unit: str = None) -> Dict:
        """
        Draw an arc by center point and angles
        
        Args:
            cx, cy: Center point
            radius: Arc radius
            start_angle: Start angle in degrees
            end_angle: End angle in degrees
            unit: Unit for radius
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err
            
            if radius <= 0:
                return self._result(
                    False,
                    f"Invalid arc radius {radius}: radius must be greater than 0.",
                    SwErrors.swInvalidParameter,
                )

            # Convert to meters
            cx_m = self._units.to_meters(cx, unit)
            cy_m = self._units.to_meters(cy, unit)
            radius_m = self._units.to_meters(radius, unit)
            
            # Convert angles to radians
            start_rad = math.radians(start_angle)
            end_rad = math.radians(end_angle)
            
            # Calculate start and end points
            x1 = cx_m + radius_m * math.cos(start_rad)
            y1 = cy_m + radius_m * math.sin(start_rad)
            x2 = cx_m + radius_m * math.cos(end_rad)
            y2 = cy_m + radius_m * math.sin(end_rad)
            
            # Draw arc (center, start, end, direction)
            arc = doc.SketchManager.CreateArc(
                cx_m, cy_m, 0,   # Center
                x1, y1, 0,       # Start
                x2, y2, 0,       # End
                1                # Direction (1 = counter-clockwise)
            )
            
            if arc is None:
                return self._result(False, "Failed - ensure sketch is active",
                                  SwErrors.swSketchError)
            
            unit_str = unit or self._units.default_unit.value
            arc_angle = abs(end_angle - start_angle)
            
            return self._result(True, f"Arc: r={radius}{unit_str}, {arc_angle}°",
                              SwErrors.swSuccess,
                              {"radius": radius, "start_angle": start_angle,
                               "end_angle": end_angle, "unit": unit_str})
            
        except Exception as e:
            logger.error(f"Draw arc error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
    
    def draw_polygon(self, cx: float = 0, cy: float = 0, radius: float = 25,
                     sides: int = 6, unit: str = None) -> Dict:
        """
        Draw a regular polygon
        
        Args:
            cx, cy: Center point
            radius: Circumscribed radius
            sides: Number of sides (3-100)
            unit: Unit for radius
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err
            
            if radius <= 0:
                return self._result(
                    False,
                    f"Invalid polygon radius {radius}: radius must be greater than 0.",
                    SwErrors.swInvalidParameter,
                )

            if sides < 3 or sides > 100:
                return self._result(False, "Sides must be between 3 and 100",
                                  SwErrors.swInvalidInput)
            
            # Convert to meters
            cx_m = self._units.to_meters(cx, unit)
            cy_m = self._units.to_meters(cy, unit)
            radius_m = self._units.to_meters(radius, unit)
            
            # Draw polygon
            polygon = doc.SketchManager.CreatePolygon(
                cx_m, cy_m, 0,               # Center
                cx_m + radius_m, cy_m, 0,    # Vertex
                sides,                        # Number of sides
                False                         # Inscribed (False = circumscribed)
            )
            
            if polygon is None:
                return self._result(False, "Failed - ensure sketch is active",
                                  SwErrors.swSketchError)
            
            unit_str = unit or self._units.default_unit.value
            
            return self._result(True, f"{sides}-sided polygon: r={radius}{unit_str}",
                              SwErrors.swSuccess,
                              {"sides": sides, "radius": radius, "unit": unit_str})
            
        except Exception as e:
            logger.error(f"Draw polygon error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
    
    def draw_centerline(self, x1: float = 0, y1: float = -50,
                        x2: float = 0, y2: float = 50, unit: str = None) -> Dict:
        """
        Draw a centerline (for revolve axis)
        
        Args:
            x1, y1: Start point
            x2, y2: End point
            unit: Unit for coordinates
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err
            
            # Convert to meters
            x1_m = self._units.to_meters(x1, unit)
            y1_m = self._units.to_meters(y1, unit)
            x2_m = self._units.to_meters(x2, unit)
            y2_m = self._units.to_meters(y2, unit)
            
            # Draw centerline
            line = doc.SketchManager.CreateCenterLine(x1_m, y1_m, 0, x2_m, y2_m, 0)
            
            if line is None:
                return self._result(False, "Failed - ensure sketch is active",
                                  SwErrors.swSketchError)
            
            return self._result(True, "Centerline created",
                              SwErrors.swSuccess)
            
        except Exception as e:
            logger.error(f"Draw centerline error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)

    def draw_text(self, text: str, x: float = 0.0, y: float = 0.0,
                  height: float = 12.0, font_name: str = "Segoe UI",
                  bold: bool = True, italic: bool = False,
                  angle: float = 0.0, center: bool = False,
                  unit: str = None) -> Dict:
        """
        Draw sketch text in the active sketch
        
        Args:
            text: String text to insert (supports \n for multi-line)
            x, y: Placement coordinates (origin of text)
            height: Font character height in units (default mm)
            font_name: Font typeface name (default 'Segoe UI')
            bold: Bold formatting
            italic: Italic formatting
            angle: Text rotation angle in degrees (e.g. 0, 90, 180, 270)
            center: Center-align text horizontally
            unit: Unit for coordinates and height (default active unit)
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            sk_err = self._require_active_sketch_in_3d(doc)
            if sk_err:
                return sk_err

            if not text:
                return self._result(
                    False,
                    "Text parameter cannot be empty.",
                    SwErrors.swInvalidParameter,
                )

            # Convert coordinates and height to meters
            x_m = self._units.to_meters(x, unit)
            y_m = self._units.to_meters(y, unit)
            height_m = self._units.to_meters(height, unit)

            formatted_text = f"<r{int(angle)}>{text}" if angle != 0.0 else text
            alignment = 1 if center else 0

            # Insert sketch text via ModelDoc2::InsertSketchText
            sketch_text = doc.InsertSketchText(x_m, y_m, 0.0, formatted_text, alignment, 0, 0, 100, 100)
            if sketch_text is None:
                return self._result(
                    False,
                    "Failed to insert sketch text - ensure sketch is active",
                    SwErrors.swSketchError,
                )

            # Customize font format if supported
            try:
                tf = None
                ole = getattr(sketch_text, "_oleobj_", None)
                if ole is not None:
                    # ISketchText::GetTextFormat is DISPID 8 in SolidWorks COM API
                    try:
                        tf_disp = ole.Invoke(8, 0, pythoncom.DISPATCH_METHOD, True)
                        tf = win32com.client.Dispatch(tf_disp)
                    except Exception:
                        get_tf = getattr(sketch_text, "GetTextFormat", None)
                        tf = get_tf() if callable(get_tf) else get_tf
                else:
                    get_tf = getattr(sketch_text, "GetTextFormat", None)
                    tf = get_tf() if callable(get_tf) else get_tf

                if tf:
                    if font_name:
                        tf.TypeFaceName = font_name
                    if height_m > 0:
                        tf.CharHeight = height_m
                    tf.Bold = bold
                    tf.Italic = italic
                    if ole is not None:
                        # ISketchText::SetTextFormat is DISPID 10: SetTextFormat(UseDoc: False, TextFormat: IDispatch)
                        try:
                            tf_ole = getattr(tf, "_oleobj_", tf)
                            ole.Invoke(10, 0, pythoncom.DISPATCH_METHOD, True, False, tf_ole)
                        except Exception:
                            set_tf = getattr(sketch_text, "SetTextFormat", None)
                            if callable(set_tf):
                                set_tf(False, tf)
                    else:
                        set_tf = getattr(sketch_text, "SetTextFormat", None)
                        if callable(set_tf):
                            set_tf(False, tf)
            except Exception as fe:
                logger.debug(f"Could not apply font formatting: {fe}")

            unit_str = unit or self._units.default_unit.value
            return self._result(
                True,
                f"Inserted sketch text: '{text[:30]}' at ({x}, {y})",
                SwErrors.swSuccess,
                {"text": text, "x": x, "y": y, "height": height, "font": font_name, "angle": angle, "center": center, "unit": unit_str},
            )
        except Exception as e:
            logger.error(f"Draw text error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swSketchError)
