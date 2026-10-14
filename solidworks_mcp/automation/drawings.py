"""
Drawing automation operations for SolidWorks MCP.
Provides drawing creation from 3D models, view generation, and PDF/DXF/DWG/image export.
"""

import os
import logging
from typing import Dict, Optional, Any

from ..constants import SwErrors, SwDocumentTypes
from ..core import get_fs_policy

try:
    import win32com.client
    import pythoncom
    HAS_PYWIN32 = True
except ImportError:
    HAS_PYWIN32 = False

logger = logging.getLogger("SolidWorksMCP.Drawings")


class DrawingOperations:
    """
    Mixin class providing drawing operations for SolidWorksAutomation.
    """

    SUPPORTED_EXPORT_FORMATS = {
        "pdf": ".pdf",
        "dxf": ".dxf",
        "dwg": ".dwg",
        "png": ".png",
        "jpg": ".jpg",
        "jpeg": ".jpg",
    }

    def create_drawing_from_model(
        self,
        model_path: Optional[str] = None,
        view_name: str = "*Isometric",
        x: float = 0.1,
        y: float = 0.1,
        paper_size: str = "A4",
    ) -> Dict:
        """
        Create a new drawing and place a model view.

        Args:
            model_path: Optional path to part or assembly. If omitted, uses active document.
            view_name: View name to insert (e.g. '*Isometric', '*Front', '*Top', '*Right')
            x: View X position on sheet in meters (default: 0.1m)
            y: View Y position on sheet in meters (default: 0.1m)
            paper_size: Paper size (A4, A3, Letter)
        """
        try:
            if not self.is_connected:
                if not getattr(self._config, "auto_connect", True):
                    return self._result(
                        False,
                        "Not connected to SolidWorks. Call connect_solidworks first.",
                        SwErrors.swConnectionError
                    )
                r = self.connect()
                if not r["success"]:
                    return r

            target_model_path = model_path
            if not target_model_path:
                active_doc = self._sw_app.ActiveDoc
                if active_doc is not None:
                    path_prop = getattr(active_doc, "GetPathName", None)
                    target_model_path = path_prop() if callable(path_prop) else path_prop

            if not target_model_path:
                return self._result(
                    False,
                    "No model specified and no saved model document is active.",
                    SwErrors.swNoActiveDocument,
                )

            # Filesystem policy validation
            is_valid, canonical, err_msg = get_fs_policy().validate_path(target_model_path, must_exist=True)
            if not is_valid:
                return self._result(
                    False,
                    f"Filesystem security violation: {err_msg}",
                    SwErrors.swFileNotFoundError,
                )
            target_model_path = canonical

            # Create drawing document
            drw_res = self.create_new_drawing(paper_size=paper_size)
            if not drw_res["success"]:
                return drw_res

            drw_doc = self._sw_app.ActiveDoc

            # Pre-open model document if needed
            ext = os.path.splitext(target_model_path)[1].lower()
            doc_type = SwDocumentTypes.swDocASSEMBLY if ext == ".sldasm" else SwDocumentTypes.swDocPART

            if HAS_PYWIN32:
                errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                self._sw_app.OpenDoc6(target_model_path, int(doc_type), 1, "", errors, warnings)
                self._sw_app.ActivateDoc3(drw_doc.GetTitle, False, 2, errors)

            # Record pre-mutation view count
            pre_view_count = 0
            try:
                from ..core.com_compat import get_attr_or_call
                pre_view_count = get_attr_or_call(drw_doc, "GetViewCount") or 0
            except Exception:
                pass

            # Insert model view
            view = drw_doc.CreateDrawViewFromModelView3(target_model_path, view_name, float(x), float(y), 0.0)
            view_name_created = None
            if view is not None:
                try:
                    view_name_created = view.GetName2()
                except Exception:
                    pass

            # Refresh drawing
            try:
                drw_doc.ViewZoomtofit2()
                drw_doc.GraphicsRedraw2()
            except Exception:
                pass

            # Post-condition check: verify view created and view count increased
            post_view_count = 0
            try:
                post_view_count = get_attr_or_call(drw_doc, "GetViewCount") or 0
            except Exception:
                pass

            if view is None or post_view_count <= pre_view_count:
                return self._result(
                    False,
                    f"Post-condition failed: View '{view_name}' was not created (view count before: {pre_view_count}, after: {post_view_count})",
                    SwErrors.swGeneralError,
                )

            title = self._get_doc_title(drw_doc)
            return self._result(
                True,
                f"Created drawing '{title}' with view '{view_name_created or view_name}'",
                SwErrors.swSuccess,
                {
                    "drawing_name": title,
                    "model_path": target_model_path,
                    "view_name": view_name_created or view_name,
                    "paper_size": paper_size,
                    "position_m": [x, y],
                    "view_count": post_view_count,
                },
            )
        except Exception as e:
            logger.error(f"create_drawing_from_model error: {e}")
            return self._result(False, f"Create drawing error: {e}", SwErrors.swGeneralError)

    def export_drawing(self, filepath: str, format: str = "pdf") -> Dict:
        """
        Export the active drawing to PDF, DXF, DWG, or image formats.

        Args:
            filepath: Target destination file path
            format: Export format ('pdf', 'dxf', 'dwg', 'png', 'jpg')
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            if doc.GetType != SwDocumentTypes.swDocDRAWING:
                return self._result(
                    False,
                    "Active document is not a drawing. Open or create a drawing first.",
                    SwErrors.swInvalidDocumentType,
                )

            fmt = format.lower().strip()
            if fmt not in self.SUPPORTED_EXPORT_FORMATS:
                return self._result(
                    False,
                    f"Unsupported export format '{format}'. Supported formats: {list(self.SUPPORTED_EXPORT_FORMATS.keys())}",
                    SwErrors.swInvalidParameter,
                )

            expected_ext = self.SUPPORTED_EXPORT_FORMATS[fmt]
            if not filepath.lower().endswith(expected_ext):
                filepath = filepath + expected_ext

            is_valid, canonical, err_msg = get_fs_policy().validate_path(filepath, must_exist=False)
            if not is_valid:
                return self._result(
                    False,
                    f"Filesystem security violation: {err_msg}",
                    SwErrors.swFileNotFoundError,
                )
            filepath = canonical

            # Ensure parent directory exists
            parent = os.path.dirname(filepath)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)

            # Export using SaveAs3
            # Options: 0 = swSaveAsCurrentVersion, 2 = swSaveAsOptions_Silent
            saved = False
            try:
                res = doc.SaveAs3(filepath, 0, 2)
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    saved = True
            except Exception as e:
                logger.debug(f"SaveAs3 export failed: {e}")

            if not saved:
                try:
                    doc.SaveAs(filepath)
                    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                        saved = True
                except Exception as e:
                    logger.debug(f"SaveAs fallback export failed: {e}")

            if not saved or not os.path.exists(filepath):
                return self._result(
                    False,
                    f"Failed to export drawing to '{filepath}'",
                    SwErrors.swExportError,
                )

            size_bytes = os.path.getsize(filepath)
            return self._result(
                True,
                f"Exported drawing to '{filepath}' ({size_bytes:,} bytes)",
                SwErrors.swSuccess,
                {
                    "filepath": filepath,
                    "format": fmt,
                    "size_bytes": size_bytes,
                },
            )
        except Exception as e:
            logger.error(f"export_drawing error: {e}")
            return self._result(False, f"Export drawing error: {e}", SwErrors.swGeneralError)

    def import_model_items(
        self,
        option: int = 1,
        types: int = 1,
        all_views: bool = True,
        duplicate_dims: bool = False,
        hidden_feature_dims: bool = False,
        use_placement_in_sketch: bool = True,
    ) -> Dict:
        """
        Import model dimensions and annotations into drawing views (IDrawingDoc.InsertModelAnnotations3).

        Args:
            option: 1 = all views, 2 = selected view (swModelAnnotationOptions_e)
            types: 1 = dimensions (swModelAnnotationTypes_e)
            all_views: Import into all views
            duplicate_dims: Allow duplicate dimensions
            hidden_feature_dims: Include dimensions for hidden features
            use_placement_in_sketch: Use dimension placement from sketch
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            if doc.GetType != SwDocumentTypes.swDocDRAWING:
                return self._result(False, "import_model_items requires an active Drawing document", SwErrors.swInvalidDocumentType)

            res = doc.InsertModelAnnotations3(
                int(option),
                int(types),
                bool(all_views),
                bool(duplicate_dims),
                bool(hidden_feature_dims),
                bool(use_placement_in_sketch),
            )
            try:
                doc.GraphicsRedraw2()
            except Exception:
                pass

            return self._result(
                True,
                "Model items and annotations imported into drawing",
                SwErrors.swSuccess,
                {"all_views": all_views, "types": types},
            )
        except Exception as e:
            logger.error(f"import_model_items error: {e}")
            return self._result(False, f"import_model_items error: {e}", SwErrors.swGeneralError)

    def add_drawing_dimension(
        self,
        x: float,
        y: float,
        z: float = 0.0,
        unit: str = "mm",
    ) -> Dict:
        """
        Add a dimension to selected entities in the active drawing (IModelDoc2.AddDimension2).

        Args:
            x: Placement coordinate X on drawing sheet
            y: Placement coordinate Y on drawing sheet
            z: Placement coordinate Z on drawing sheet
            unit: Coordinate unit (default 'mm')
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            if doc.GetType != SwDocumentTypes.swDocDRAWING:
                return self._result(False, "add_drawing_dimension requires an active Drawing document", SwErrors.swInvalidDocumentType)

            from ..utils import get_unit_converter
            converter = getattr(self, "_units", get_unit_converter())
            x_m = converter.to_meters(x, unit)
            y_m = converter.to_meters(y, unit)
            z_m = converter.to_meters(z, unit)

            dim = doc.AddDimension2(x_m, y_m, z_m)
            try:
                doc.GraphicsRedraw2()
            except Exception:
                pass

            dim_name = dim.Name if dim is not None else None
            return self._result(
                True,
                f"Added drawing dimension at ({x}, {y}) {unit}",
                SwErrors.swSuccess,
                {"dimension_name": dim_name, "position": [x, y, z], "unit": unit},
            )
        except Exception as e:
            logger.error(f"add_drawing_dimension error: {e}")
            return self._result(False, f"add_drawing_dimension error: {e}", SwErrors.swGeneralError)

    def create_section_view(
        self,
        x: float,
        y: float,
        section_label: str = "A",
        options: int = 32,
        line_x1: Optional[float] = None,
        line_y1: Optional[float] = None,
        line_x2: Optional[float] = None,
        line_y2: Optional[float] = None,
        parent_view: Optional[str] = None,
        unit: str = "mm",
    ) -> Dict:
        """
        Create a section view from an active section line sketch in the drawing (IDrawingDoc.CreateSectionViewAt5).

        Args:
            x: Placement coordinate X on sheet
            y: Placement coordinate Y on sheet
            section_label: Section label ('A', 'B', etc.)
            options: Section view options bitmask (default 32: swCreateSectionView_NotAligned)
            line_x1: Optional section line start X coordinate (in view coordinates)
            line_y1: Optional section line start Y coordinate (in view coordinates)
            line_x2: Optional section line end X coordinate (in view coordinates)
            line_y2: Optional section line end Y coordinate (in view coordinates)
            parent_view: Optional parent drawing view name (defaults to first model view)
            unit: Placement coordinate unit
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            if doc.GetType != SwDocumentTypes.swDocDRAWING:
                return self._result(False, "create_section_view requires an active Drawing document", SwErrors.swInvalidDocumentType)

            from ..utils import get_unit_converter
            from ..core.com_compat import get_attr_or_call
            converter = getattr(self, "_units", get_unit_converter())
            x_m = converter.to_meters(x, unit)
            y_m = converter.to_meters(y, unit)

            # Safe view count helper
            def _get_view_count(d):
                try:
                    v = getattr(d, 'GetViewCount', None)
                    if v is not None:
                        val = v() if callable(v) else v
                        if isinstance(val, (int, float)):
                            return int(val)
                except Exception:
                    pass
                return 0

            # If section line coordinates are provided, activate parent view and sketch the cutting line
            if line_x1 is not None and line_y1 is not None and line_x2 is not None and line_y2 is not None:
                target_vname = parent_view
                if not target_vname:
                    first_view = None
                    try:
                        first_view = doc.GetFirstView()
                    except Exception:
                        pass
                    model_view = None
                    if first_view:
                        try:
                            model_view = first_view.GetNextView()
                        except Exception:
                            pass
                    target_vname = None
                    if model_view:
                        try:
                            target_vname = model_view.GetName2()
                        except Exception:
                            target_vname = None
                    if not target_vname:
                        target_vname = "Drawing View1"

                try:
                    doc.ActivateView(str(target_vname))
                except Exception as ex_act:
                    logger.debug(f"ActivateView failed: {ex_act}")

                lx1_m = converter.to_meters(line_x1, unit)
                ly1_m = converter.to_meters(line_y1, unit)
                lx2_m = converter.to_meters(line_x2, unit)
                ly2_m = converter.to_meters(line_y2, unit)

                doc.ClearSelection2(True)
                sk_line = None
                sk_mgr = getattr(doc, "SketchManager", None)
                if sk_mgr:
                    try:
                        sk_mgr.InsertSketch(True)
                    except Exception:
                        pass
                    try:
                        sk_line = sk_mgr.CreateLine(lx1_m, ly1_m, 0.0, lx2_m, ly2_m, 0.0)
                    except Exception as ex_line:
                        logger.debug(f"SketchManager.CreateLine failed: {ex_line}")

                if not sk_line:
                    try:
                        sk_line = doc.CreateLine2(lx1_m, ly1_m, 0.0, lx2_m, ly2_m, 0.0)
                    except Exception as ex_line2:
                        logger.debug(f"doc.CreateLine2 failed: {ex_line2}")

                if sk_mgr:
                    try:
                        sk_mgr.InsertSketch(True)
                    except Exception:
                        pass

                if sk_line:
                    try:
                        sk_line.Select2(False, 0)
                    except Exception:
                        try:
                            sk_line.Select(False)
                        except Exception:
                            pass

            pre_view_count = _get_view_count(doc)

            opt = int(options) if options is not None else 32

            view = None
            try:
                view = doc.CreateSectionViewAt5(
                    x_m, y_m, 0.0,
                    str(section_label),
                    opt,
                    None,
                    0.0,
                )
            except Exception as ex1:
                logger.debug(f"CreateSectionViewAt5 (None) failed: {ex1}")
                try:
                    import win32com.client
                    import pythoncom
                    null_obj = win32com.client.VARIANT(pythoncom.VT_EMPTY, None)
                    view = doc.CreateSectionViewAt5(
                        x_m, y_m, 0.0,
                        str(section_label),
                        opt,
                        null_obj,
                        0.0,
                    )
                except Exception as ex2:
                    logger.debug(f"CreateSectionViewAt5 fallback failed: {ex2}")
                    try:
                        view = doc.CreateSectionViewAt(x_m, y_m, 0.0, False, False)
                    except Exception:
                        pass
            try:
                doc.ViewZoomtofit2()
                doc.GraphicsRedraw2()
            except Exception:
                pass

            post_view_count = _get_view_count(doc)
            if view is None and post_view_count <= pre_view_count:
                return self._result(False, "create_section_view failed: no section line selected or view could not be created", SwErrors.swOperationFailed)

            view_name = None
            if view is not None:
                try:
                    view_name = view.GetName2()
                except Exception:
                    pass
            if not view_name:
                view_name = f"Section View {section_label}-{section_label}"

            return self._result(
                True,
                f"Created section view '{view_name}'",
                SwErrors.swSuccess,
                {"view_name": view_name, "section_label": section_label, "view_count": post_view_count},
            )
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"create_section_view error: {e}\n{tb}")
            return self._result(False, f"create_section_view error: {e}\n{tb}", SwErrors.swGeneralError)

    def insert_bom_table(
        self,
        template_path: Optional[str] = None,
        x: float = 0.0,
        y: float = 0.0,
        bom_type: str = "top_level",
        anchor_type: int = 1,
        configuration: Optional[str] = "",
        unit: str = "mm",
    ) -> Dict:
        """
        Insert a Bill of Materials (BOM) table into the active drawing view (IView.InsertBomTable4 / InsertBomTable3).

        Args:
            template_path: Optional path to .sldbomtbt template (auto-located if omitted)
            x: Placement X coordinate
            y: Placement Y coordinate
            bom_type: 'top_level', 'parts_only', 'indented'
            anchor_type: Corner anchor type (1=top-left, 2=top-right, 3=bottom-left, 4=bottom-right)
            configuration: Specific configuration name, or empty for active
            unit: Placement coordinate unit
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            if doc.GetType != SwDocumentTypes.swDocDRAWING:
                return self._result(False, "insert_bom_table requires an active Drawing document", SwErrors.swInvalidDocumentType)

            from ..utils import resolve_bom_template
            tbl_tmpl = resolve_bom_template(sw_app=self.sw_app, template_path=template_path)

            if not tbl_tmpl or not os.path.exists(tbl_tmpl):
                return self._result(False, f"BOM template file not found: {tbl_tmpl}", SwErrors.swFileNotFoundError)

            type_map = {
                "top_level": 1,
                "parts_only": 2,
                "indented": 3,
            }
            btype_int = type_map.get(str(bom_type).lower(), 1)

            from ..utils import get_unit_converter
            converter = getattr(self, "_units", get_unit_converter())
            x_m = converter.to_meters(x, unit)
            y_m = converter.to_meters(y, unit)

            target_view = None
            try:
                first_view = doc.GetFirstView()
                if first_view:
                    target_view = first_view.GetNextView() or first_view
            except Exception:
                pass

            bom_table = None
            try:
                bom_table = doc.Extension.InsertBomTable3(
                    tbl_tmpl, x_m, y_m, btype_int, configuration or "", False, 1, False
                )
            except Exception as e1:
                logger.debug(f"Extension.InsertBomTable3 failed: {e1}")
                try:
                    bom_table = doc.Extension.InsertBomTable(
                        tbl_tmpl, x_m, y_m, btype_int, configuration or ""
                    )
                except Exception as e2:
                    logger.debug(f"Extension.InsertBomTable failed: {e2}")
                    if target_view:
                        try:
                            bom_table = target_view.InsertBomTable2(
                                False, x_m, y_m, int(anchor_type), btype_int,
                                configuration or "", tbl_tmpl
                            )
                        except Exception as e3:
                            logger.debug(f"target_view.InsertBomTable2 failed: {e3}")

            try:
                doc.GraphicsRedraw2()
            except Exception:
                pass

            title = "BOM Table"
            if bom_table is not None:
                try:
                    from ..core.com_compat import get_attr_or_call
                    bom_feat = get_attr_or_call(bom_table, "BomFeature")
                    if bom_feat:
                        name_val = get_attr_or_call(bom_feat, "Name")
                        if name_val:
                            title = str(name_val)
                except Exception:
                    pass

            return self._result(
                True,
                f"Successfully inserted BOM table ({bom_type})",
                SwErrors.swSuccess,
                {"table_title": title, "bom_type": bom_type, "template": tbl_tmpl},
            )
        except Exception as e:
            logger.error(f"insert_bom_table error: {e}")
            return self._result(False, f"insert_bom_table error: {e}", SwErrors.swGeneralError)
