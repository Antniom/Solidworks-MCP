"""
SolidWorks Document Operations
------------------------------
Create, open, save, and manage SolidWorks documents.
"""

import os
import logging
import traceback
from typing import Optional, Dict, Any

import win32com.client
import pythoncom

from ..constants import SwErrors, SwDocumentTypes, SwFileTypes
from ..utils import find_template
from ..core.com_compat import byref_int, get_attr_or_call

logger = logging.getLogger(__name__)


class DocumentOperations:
    """
    Mixin class for document operations
    
    Requires parent class to have:
    - self._sw_app: SolidWorks application object
    - self.is_connected: Connection status property
    - self.connect(): Connection method
    - self._result(): Result factory method
    - self._units: UnitConverter instance
    """
    
    def _get_template(self, template_type: str) -> str:
        """Get template path with caching to eliminate disk walk latency."""
        if not hasattr(self, "_template_cache"):
            self._template_cache = {}
        if template_type not in self._template_cache:
            tmpl = self._resolve_template(template_type)
            if tmpl:  # never cache a miss: the user may fix the setting and retry
                self._template_cache[template_type] = tmpl
            logger.info(f"{template_type} template: {tmpl or 'NOT FOUND'}")
            return tmpl
        return self._template_cache[template_type]

    def _new_document_failed(self, template_type: str, template: str) -> Dict:
        """A NewDocument failure, explained: almost always a missing or unreadable template."""
        self._template_cache = {}  # re-resolve next time (the user may fix the setting)
        ext = {"part": ".prtdot", "assembly": ".asmdot", "drawing": ".drwdot"}[template_type]
        if template:
            why = (f"SolidWorks could not create a {template_type} from the template '{template}'. It may be from a "
                   f"newer SolidWorks version or damaged.")
        else:
            why = (f"No {template_type} template ({ext}) was found on this computer: not in SolidWorks' Default "
                   f"Templates setting, not via SolidWorks itself, not in the File Locations > Document Templates "
                   f"folders, and not in ProgramData\\SOLIDWORKS\\SOLIDWORKS 20xx\\templates.")
        return self._result(
            False,
            f"Failed to create {template_type}. {why} Fix: in SolidWorks open Tools > Options > System Options > "
            f"Default Templates and pick a {template_type} template (or reinstall the SolidWorks templates), then retry.",
            SwErrors.swTemplateNotFound,
            {"template_tried": template or None, "template_type": template_type},
        )

    def _resolve_template(self, template_type: str) -> str:
        """
        Find a document template on THIS machine, whatever the SolidWorks version or language.
        Order: the user's default-template setting; SolidWorks' own answer (GetDocumentTemplate);
        the template folders set in File Locations; any ProgramData SOLIDWORKS 20xx\\templates folder
        and the install's lang\\*\\templates; finally the old fixed list. The setting used to be read
        with IDs 1/2/3, which are the Documents / palette folders, not the templates (8/9/10).
        """
        import glob
        ext = {"part": ".prtdot", "assembly": ".asmdot", "drawing": ".drwdot"}.get(template_type)
        if not ext:
            return ""
        app = getattr(self, "_sw_app", None)
        if app is not None:
            try:  # swDefaultTemplatePart / Assembly / Drawing
                v = app.GetUserPreferenceStringValue({"part": 8, "assembly": 9, "drawing": 10}[template_type])
                if v and os.path.isfile(v):
                    return v
            except Exception:
                pass
            try:  # SolidWorks' own choice (honours the "always use default templates" options)
                v = app.GetDocumentTemplate({"part": 1, "assembly": 2, "drawing": 3}[template_type], "", 0, 0.0, 0.0)
                if v and os.path.isfile(str(v)):
                    return str(v)
            except Exception:
                pass

        def pick(paths):
            files = [p for p in paths if os.path.isfile(p) and p.lower().endswith(ext)]
            if not files:
                return ""
            stem = {"part": "part", "assembly": "assembly", "drawing": "drawing"}[template_type]
            files.sort(key=lambda p: (os.path.splitext(os.path.basename(p))[0].lower() != stem,
                                      "\\mbd\\" in p.lower(), len(p)))
            return files[0]

        folders = []
        if app is not None:
            try:  # swFileLocationsDocumentTemplates: ';'-separated folder list
                v = app.GetUserPreferenceStringValue(6)
                folders += [f for f in str(v or "").split(";") if f.strip()]
            except Exception:
                pass
        pdata = os.environ.get("ProgramData", r"C:\ProgramData")
        folders += sorted(glob.glob(os.path.join(pdata, "SOLIDWORKS", "SOLIDWORKS 20*", "templates")), reverse=True)
        try:
            from ..utils import SolidWorksFinder as _F  # install dir: lang\<any>\templates, data\templates
            exe = _F.find()
            if exe:
                base = os.path.dirname(exe)
                folders += glob.glob(os.path.join(base, "lang", "*", "templates")) + [os.path.join(base, "data", "templates")]
        except Exception:
            pass
        for folder in folders:
            hit = pick(glob.glob(os.path.join(folder.strip(), "*" + ext)) +
                       glob.glob(os.path.join(folder.strip(), "*" + ext.upper())))
            if hit:
                return hit
        return find_template(template_type) or ""

    def create_new_part(self) -> Dict:
        """
        Create a new part document
        
        Returns:
            Result dictionary with document info
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
            
            # Find part template (cached)
            template = self._get_template("part")
            if not template:
                template = ""  # Let SolidWorks use default
                logger.info("Using SolidWorks default part template")
            else:
                logger.info(f"Using template: {template}")
            
            # Create document
            doc = self._sw_app.NewDocument(template, 0, 0, 0)
            
            if doc is None:
                return self._new_document_failed("part", template)
            
            # Set view and ensure visibility in foreground
            try:
                from .base import ensure_visible_and_foreground
                ensure_visible_and_foreground(self._sw_app)
                doc.ShowNamedView2("*Isometric", 7)
                doc.ViewZoomtofit2()
                ext = getattr(doc, "Extension", None)
                if ext and hasattr(ext, "Maximize"):
                    ext.Maximize()
                elif hasattr(doc, "ActiveView") and doc.ActiveView:
                    doc.ActiveView.FrameState = 1
            except Exception:
                pass
            
            title = self._get_doc_title(doc)
            
            return self._result(True, f"Created part: {title}",
                              SwErrors.swSuccess,
                              {"name": title, "type": "Part"})
            
        except Exception as e:
            logger.error(f"Create part error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swFileLoadError)
    
    def create_new_assembly(self) -> Dict:
        """
        Create a new assembly document
        
        Returns:
            Result dictionary with document info
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
            
            template = self._get_template("assembly")
            if not template:
                template = ""
            
            doc = self._sw_app.NewDocument(template, 0, 0, 0)
            
            if doc is None:
                return self._new_document_failed("assembly", template)
            
            try:
                doc.ShowNamedView2("*Isometric", 7)
                doc.ViewZoomtofit2()
            except Exception:
                pass
            
            title = self._get_doc_title(doc)
            
            return self._result(True, f"Created assembly: {title}",
                              SwErrors.swSuccess,
                              {"name": title, "type": "Assembly"})
            
        except Exception as e:
            logger.error(f"Create assembly error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swFileLoadError)
    
    def create_new_drawing(self, paper_size: str = "A4") -> Dict:
        """
        Create a new drawing document
        
        Args:
            paper_size: Paper size (A4, A3, A2, A1, Letter)
        
        Returns:
            Result dictionary with document info
        """
        VALID_PAPER_SIZES = ["A4", "A3", "A2", "A1", "A0", "Letter", "Legal"]
        if (paper_size or "").strip().upper() not in [p.upper() for p in VALID_PAPER_SIZES]:
            return self._result(
                False,
                f"Invalid paper size '{paper_size}'. Valid paper sizes are: {', '.join(VALID_PAPER_SIZES)}",
                SwErrors.swInvalidParameter,
            )

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
            
            template = self._get_template("drawing")
            if not template:
                template = ""
            
            doc = self._sw_app.NewDocument(template, 0, 0, 0)
            
            if doc is None:
                return self._new_document_failed("drawing", template)
            
            title = self._get_doc_title(doc)
            
            return self._result(True, f"Created drawing: {title}",
                              SwErrors.swSuccess,
                              {"name": title, "type": "Drawing", "paper_size": paper_size})
            
        except Exception as e:
            logger.error(f"Create drawing error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swFileLoadError)
    
    NEUTRAL_IMPORT_EXTENSIONS = {".step", ".stp", ".stl", ".iges", ".igs", ".x_t", ".x_b", ".sat"}

    def _import_neutral_file(self, filepath: str):
        """
        Import a STEP/IGES/Parasolid/... file as a new unsaved document (ISldWorks.LoadFile4).
        Returns (doc, None) or (None, error message).
        """
        import_data = None
        try:
            import_data = self._sw_app.GetImportFileData(filepath)
        except Exception as ex:
            logger.debug(f"GetImportFileData failed: {ex}")
        if import_data is None:
            import_data = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        errors = byref_int(0)
        try:
            doc = self._sw_app.LoadFile4(filepath, "r", import_data, errors)
        except Exception as ex:
            return None, f"Import of '{filepath}' failed: {ex}"
        if doc is None:
            code = int(getattr(errors, "value", 0) or 0)
            hint = ""
            try:
                with open(filepath, "r", errors="ignore") as fh:
                    head = fh.read(400000)
                if "GEOMETRICALLY_BOUNDED_WIREFRAME" in head and "MANIFOLD_SOLID_BREP" not in head \
                        and "ADVANCED_FACE" not in head:
                    hint = (" The file contains only wireframe curves (no solid or surface bodies), so there "
                            "is nothing SolidWorks can import as a part.")
            except Exception:
                pass
            return None, (
                f"SolidWorks could not import '{os.path.basename(filepath)}' (LoadFile4 error {code}"
                f"{' = swGenericError' if code == 1 else ''}): the translator read no usable geometry.{hint}")
        return doc, None

    def native_copy_of_neutral_file(self, filepath: str) -> Dict:
        """
        Make (or reuse) a native .SLDPRT/.SLDASM copy of a STEP/IGES/... file, saved next to it with
        the same base name. Returns a result whose data has native_path, created and type.
        """
        folder, base = os.path.split(filepath)
        stem = os.path.splitext(base)[0]
        for ext in (".SLDPRT", ".SLDASM"):
            cand = os.path.join(folder, stem + ext)
            if os.path.exists(cand) and os.path.getmtime(cand) >= os.path.getmtime(filepath):
                return self._result(True, f"Using existing native copy '{cand}'", SwErrors.swSuccess,
                                    {"native_path": cand, "created": False,
                                     "type": "Assembly" if ext == ".SLDASM" else "Part"})
        doc, import_err = self._import_neutral_file(filepath)
        if doc is None:
            return self._result(False, import_err, SwErrors.swFileLoadError)
        dtype = self.get_document_type(doc)
        native = os.path.join(folder, stem + (".SLDASM" if dtype == "Assembly" else ".SLDPRT"))
        errors = byref_int(0)
        warnings = byref_int(0)
        ok = False
        detail = ""
        try:
            null = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
            ok = bool(doc.Extension.SaveAs3(native, 0, 1, null, null, errors, warnings))  # 1 = silent
        except Exception as ex:
            detail = str(ex)
            try:
                ok = bool(doc.SaveAs3(native, 0, 1) == 0) and os.path.exists(native)
            except Exception as ex2:
                detail += f"; {ex2}"
        if not ok or not os.path.exists(native):
            return self._result(
                False,
                f"Imported '{base}' but could not save the native copy '{native}' "
                f"(errors={getattr(errors, 'value', '?')}). {detail}".strip(),
                SwErrors.swFileSaveError)
        return self._result(True, f"Imported '{base}' and saved native copy '{native}'", SwErrors.swSuccess,
                            {"native_path": native, "created": True, "type": dtype})

    def open_document(self, filepath: str) -> Dict:
        """
        Open an existing document
        
        Args:
            filepath: Path to SolidWorks file
        
        Returns:
            Result dictionary
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
            
            from ..core import get_fs_policy
            is_valid, canonical, err_msg = get_fs_policy().validate_path(filepath, must_exist=True)
            if not is_valid:
                return self._result(False, f"Filesystem security violation: {err_msg}",
                                  SwErrors.swFileNotFoundError)
            filepath = canonical
            
            # Determine document type from extension
            ext = os.path.splitext(filepath)[1].lower()

            doc = None
            if ext in self.NEUTRAL_IMPORT_EXTENSIONS:
                doc, import_err = self._import_neutral_file(filepath)
                if doc is None:
                    # OpenDoc6 cannot read these formats; falling back to it only hid the real reason.
                    return self._result(False, import_err, SwErrors.swFileLoadError)
                title = self._get_doc_title(doc)
                dtype = self.get_document_type(doc)
                native_ext = ".SLDASM" if dtype == "Assembly" else ".SLDPRT"
                return self._result(
                    True,
                    f"Imported {ext} file as a new unsaved {dtype} '{title}'. It has no file yet: call "
                    f"save_document with a {native_ext} path to keep a native copy.",
                    SwErrors.swSuccess,
                    {"name": title, "path": filepath, "imported": True, "type": dtype, "saved": False})

            if doc is None:
                type_map = {
                    ".sldprt": SwDocumentTypes.swDocPART,
                    ".sldasm": SwDocumentTypes.swDocASSEMBLY,
                    ".slddrw": SwDocumentTypes.swDocDRAWING,
                }
                doc_type = type_map.get(ext, SwDocumentTypes.swDocPART)
                
                # Open document with silent flag (1 = swOpenDocOptions_Silent)
                errors = byref_int(0)
                warnings = byref_int(0)
                
                doc = self._sw_app.OpenDoc6(filepath, int(doc_type), 1, "", errors, warnings)
                err_val = getattr(errors, "value", 0)
                if doc is None or err_val != 0:
                    return self._result(False, f"Failed to open (error {err_val})",
                                      SwErrors.swFileLoadError)
            
            title = self._get_doc_title(doc)

            # A document that is already loaded as a component of an open assembly is returned by
            # OpenDoc6 without becoming the active document, so the next part tools edited the
            # assembly instead (MCP-030). Activate it and confirm.
            def _active_path() -> str:
                try:
                    act = self._sw_app.ActiveDoc
                    return os.path.normcase(os.path.abspath(str(self._get_doc_path(act) or ""))) if act is not None else ""
                except Exception:
                    return ""

            want = os.path.normcase(os.path.abspath(filepath))
            if _active_path() != want:
                for name in (title, os.path.basename(filepath), filepath):
                    try:
                        self._sw_app.ActivateDoc3(name, False, 2, byref_int(0))  # 2 = don't rebuild
                    except Exception:
                        pass
                    if _active_path() == want:
                        break
            if _active_path() != want:
                active_title = ""
                try:
                    active_title = self._get_doc_title(self._sw_app.ActiveDoc)
                except Exception:
                    pass
                return self._result(
                    False,
                    f"'{title}' is loaded but could not be made the active document (active: '{active_title}'). "
                    "Tools act on the active document, so nothing further was done.",
                    SwErrors.swFileLoadError, {"name": title, "path": filepath, "active_document": active_title})

            return self._result(True, f"Opened and active: {title}",
                              SwErrors.swSuccess,
                              {"name": title, "path": filepath, "active": True})
            
        except Exception as e:
            logger.error(f"Open document error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swFileLoadError)
    
    def _export_neutral(self, doc, filepath: str) -> Dict:
        """
        Export a copy of the active part/assembly to a neutral format chosen by extension
        (STEP, IGES, STL, Parasolid, 3MF ...). The open document keeps its own file name.
        """
        from ..core import get_fs_policy
        is_valid, canonical, err_msg = get_fs_policy().validate_path(filepath, must_exist=False)
        if not is_valid:
            return self._result(False, f"Filesystem security violation: {err_msg}", SwErrors.swFileSaveError)
        filepath = canonical
        dir_path = os.path.dirname(filepath)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)  # so the existence check below proves this export wrote it
            except OSError:
                pass

        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        empty_export = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
        ok = False
        detail = ""
        try:
            # SaveAs3 options: 1 = swSaveAsOptions_Silent | 2 = swSaveAsOptions_Copy
            ok = bool(doc.Extension.SaveAs3(filepath, 0, 1 | 2, empty_export, empty_export, errors, warnings))
        except Exception as e1:
            detail = f"Extension.SaveAs3: {e1}"
            try:
                ok = bool(doc.Extension.SaveAs(filepath, 0, 1 | 2, empty_export, errors, warnings))
            except Exception as e2:
                detail += f"; Extension.SaveAs: {e2}"
        size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        if not ok or size == 0:
            return self._result(
                False,
                f"Export to '{filepath}' failed (errors={getattr(errors, 'value', '?')}, "
                f"warnings={getattr(warnings, 'value', '?')}). {detail}".strip(),
                SwErrors.swFileSaveError,
            )
        return self._result(
            True,
            f"Exported copy to '{filepath}' ({size:,} bytes)",
            SwErrors.swSuccess,
            {"path": filepath, "size_bytes": size, "method": "Extension.SaveAs3 (copy)",
             "warnings": getattr(warnings, "value", 0)},
        )

    def save_document(self, filepath: str = None) -> Dict:
        """
        Save the active document
        FIXED v4.1: Use doc.SaveAs() as primary method (avoids COM type mismatch
        with Extension.SaveAs where None parameter fails in SW 2025).
        
        Args:
            filepath: Path to save (None = save in place)
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            
            if filepath:
                # Validate extension matches active document type
                ext = os.path.splitext(filepath)[1].lower()
                dtype = self.get_document_type(doc)
                expected_ext = {
                    "Part": ".sldprt",
                    "Assembly": ".sldasm",
                    "Drawing": ".slddrw",
                }.get(dtype)
                export_exts = {
                    "Part": {".step", ".stp", ".igs", ".iges", ".stl", ".x_t", ".x_b", ".3mf", ".obj", ".ply", ".sat", ".parasolid"},
                    "Assembly": {".step", ".stp", ".igs", ".iges", ".stl", ".x_t", ".x_b", ".3mf", ".sat"},
                }.get(dtype, set())
                if ext in export_exts:
                    return self._export_neutral(doc, filepath)
                if expected_ext and ext != expected_ext:
                    return self._result(
                        False,
                        f"File extension '{ext}' does not match active document type '{dtype}' (expected '{expected_ext}')",
                        SwErrors.swInvalidInput
                    )

                from ..core import get_fs_policy
                is_valid, canonical, err_msg = get_fs_policy().validate_path(filepath, must_exist=False)
                if not is_valid:
                    return self._result(False, f"Filesystem security violation: {err_msg}",
                                      SwErrors.swFileSaveError)
                filepath = canonical
                
                # Ensure directory exists
                dir_path = os.path.dirname(filepath)
                if dir_path and not os.path.exists(dir_path):
                    os.makedirs(dir_path)
                
                saved = False
                method_used = ""
                
                # Method 0: doc.SaveAs3 (standard SW 2025 SaveAs with silent options)
                try:
                    # Options: swSaveAsOptions_Silent = 1 (do not use 2, which is swSaveAsOptions_Copy)
                    res = doc.SaveAs3(filepath, 0, 1)
                    if res == 0:
                        saved = True
                        method_used = "SaveAs3"
                except Exception as e:
                    logger.debug(f"doc.SaveAs3 failed: {e}")

                # Method 1: doc.SaveAs
                if not saved:
                    try:
                        result = doc.SaveAs(filepath)
                        if result:
                            saved = True
                            method_used = "SaveAs"
                    except Exception as e:
                        logger.debug(f"doc.SaveAs failed: {e}")
                
                # Method 2: Extension.SaveAs with proper VARIANT null dispatch
                if not saved:
                    try:
                        empty_export = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
                        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                        
                        result = doc.Extension.SaveAs(
                            filepath, 0, 0, empty_export, errors, warnings
                        )
                        if result and errors.value == 0:
                            saved = True
                            method_used = "Extension.SaveAs"
                    except Exception as e:
                        logger.debug(f"Extension.SaveAs failed: {e}")
                
                # Method 3: Extension.SaveAs2
                if not saved:
                    try:
                        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                        
                        result = doc.Extension.SaveAs2(
                            filepath, 0, 0, None, "", False, errors, warnings
                        )
                        if result:
                            saved = True
                            method_used = "Extension.SaveAs2"
                    except Exception as e:
                        logger.debug(f"Extension.SaveAs2 failed: {e}")
                
                if not saved:
                    return self._result(False, "Save failed - all methods attempted",
                                      SwErrors.swFileSaveError)
                
                return self._result(True, f"Saved: {filepath} [{method_used}]",
                                  SwErrors.swSuccess,
                                  {"path": filepath, "method": method_used})
            else:
                # Save in place
                doc_path = self._get_doc_path(doc)
                if not doc_path:
                    return self._result(False, "Document has never been saved to disk. Provide filepath for the initial save.",
                                      SwErrors.swFileSaveError)
                
                saved = False
                err_code = 0
                try:
                    errors = byref_int(0)
                    warnings = byref_int(0)
                    _ = doc.Save3(1, errors, warnings)
                    if getattr(errors, "value", 0) == 0:
                        saved = True
                    else:
                        err_code = getattr(errors, "value", 0)
                except Exception as ex:
                    logger.debug(f"doc.Save3 failed: {ex}")

                if not saved:
                    try:
                        doc.Save()
                        saved = True
                    except Exception as ex:
                        logger.debug(f"doc.Save failed: {ex}")

                if not saved:
                    return self._result(False, f"Save failed (error code {err_code})",
                                      SwErrors.swFileSaveError)
                
                path = self._get_doc_path(doc)
                return self._result(True, f"Saved: {path}",
                                  SwErrors.swSuccess, {"path": path})
            
        except Exception as e:
            logger.error(f"Save error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swFileSaveError)
    
    def close_document(self, save: bool = False) -> Dict:
        """
        Close the active document
        
        Args:
            save: Save before closing
        
        Returns:
            Result dictionary
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            
            title = self._get_doc_title(doc)
            if not title:
                return self._result(False, "No active document open to close.", SwErrors.swNoActiveDocument)
            
            if save:
                path = doc.GetPathName if not callable(getattr(doc, "GetPathName", None)) else doc.GetPathName()
                if not path or not str(path).strip():
                    return self._result(
                        False,
                        f"Save failed: Active document '{title}' has no file path (unsaved document). Document was not closed.",
                        SwErrors.swFileSaveError,
                        {"document": title, "path": path}
                    )
                errors = byref_int(0)
                warnings = byref_int(0)
                try:
                    save_res = doc.Save3(1, errors, warnings)
                    err_val = getattr(errors, "value", 0)
                    warn_val = getattr(warnings, "value", 0)
                    if not save_res or err_val != 0 or warn_val != 0:
                        return self._result(
                            False,
                            f"Save failed (Save3 result={save_res}, errors={err_val}, warnings={warn_val}). Document was not closed.",
                            SwErrors.swFileSaveError,
                            {"document": title, "errors": err_val, "warnings": warn_val}
                        )
                except Exception as save_err:
                    return self._result(
                        False,
                        f"Save raised exception: {save_err}. Document was not closed.",
                        SwErrors.swFileSaveError,
                        {"document": title, "exception": str(save_err)}
                    )
                self._sw_app.CloseDoc(title)
            else:
                try:
                    self._sw_app.QuitDoc(title)
                except Exception:
                    self._sw_app.CloseDoc(title)
            
            return self._result(True, f"Closed: {title}",
                              SwErrors.swSuccess, {"document": title})
            
        except Exception as e:
            logger.error(f"Close error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swGeneralError)
    
    def get_document_info(self) -> Dict:
        """
        Get information about the active document
        
        Returns:
            Result dictionary with document details
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err
            
            type_names = {
                0: "None",
                1: "Part",
                2: "Assembly",
                3: "Drawing"
            }
            
            dtype_raw = getattr(doc, "GetType", 0)
            doc_type = dtype_raw() if callable(dtype_raw) else dtype_raw
            title = self._get_doc_title(doc)
            path = self._get_doc_path(doc)
            
            info = {
                "title": title,
                "path": path if path else "Not saved",
                "type": type_names.get(doc_type, "Unknown"),
                "type_code": doc_type,
            }
            
            return self._result(True, f"{title} ({info['type']})",
                              SwErrors.swSuccess, info)
            
        except Exception as e:
            logger.error(f"Get info error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swGeneralError)
    
    def list_open_documents(self) -> Dict:
        """
        List all open documents
        
        Returns:
            Result dictionary with document list
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
            
            docs = []
            type_names = {1: "Part", 2: "Assembly", 3: "Drawing"}
            
            # Do not swallow failures here: an empty list from a dead COM handle
            # used to be reported as "0 document(s) open".
            try:
                raw_docs = self._sw_app.GetDocuments
                if callable(raw_docs):
                    raw_docs = raw_docs()
            except Exception as gd_ex:
                return self._result(
                    False,
                    f"Could not query open documents (SolidWorks connection problem): {gd_ex}. "
                    "Try connect_solidworks.",
                    SwErrors.swConnectionError,
                )

            for doc in (raw_docs or []):
                try:
                    title = doc.GetTitle
                    if callable(title):
                        title = title()
                    dtype_raw = getattr(doc, "GetType", 0)
                    doc_type = dtype_raw() if callable(dtype_raw) else dtype_raw
                    
                    docs.append({
                        "title": str(title),
                        "type": type_names.get(doc_type, "Unknown")
                    })
                except Exception:
                    pass
            
            from ..core.memory_watch import memory_status, solidworks_pid
            mem = memory_status(solidworks_pid(self._sw_app, getattr(self, "_connected_pid", None)))
            msg = f"{len(docs)} document(s) open"
            if mem.get("solidworks_private_mb") is not None:
                msg += (f"; SolidWorks memory {mem['solidworks_private_mb']} MB, "
                        f"system free {mem['system_free_mb']} of {mem['system_total_mb']} MB")
            return self._result(True, msg, SwErrors.swSuccess, {"documents": docs, "memory": mem})

        except Exception as e:
            logger.error(f"List documents error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swGeneralError)

    def restart_solidworks(self, discard_unsaved: bool = False, timeout_s: float = 90.0) -> Dict:
        """
        Close every document, quit SolidWorks and start a fresh instance (MCP-050: SolidWorks keeps
        memory from closed documents and terminates when it runs out). Refuses when a document has
        unsaved changes unless discard_unsaved=True.
        """
        import gc
        import time
        from ..core.memory_watch import memory_status, solidworks_pid, wait_for_exit
        try:
            if not self.is_connected:
                r = self.connect()
                if not r.get("success"):
                    return r
            app = self._sw_app
            pid = solidworks_pid(app, getattr(self, "_connected_pid", None))
            before = memory_status(pid)
            dirty, titles = [], []
            try:
                raw = app.GetDocuments
                raw = raw() if callable(raw) else raw
            except Exception:
                raw = []
            for d in (raw or []):
                try:
                    t = str(get_attr_or_call(d, "GetTitle"))
                    titles.append(t)
                    if bool(get_attr_or_call(d, "GetSaveFlag")):
                        dirty.append(t)
                except Exception:
                    pass
            if dirty and not discard_unsaved:
                return self._result(
                    False,
                    f"Not restarted: {len(dirty)} document(s) have unsaved changes: {', '.join(dirty[:10])}. "
                    "Save them (or pass discard_unsaved=true to lose those changes).",
                    SwErrors.swInvalidParameter,
                    {"unsaved_documents": dirty, "memory_before": before},
                )
            try:
                app.CloseAllDocuments(True)
            except Exception as ex:
                logger.debug(f"CloseAllDocuments failed: {ex}")
            try:
                app.ExitApp()
            except Exception as ex:
                logger.debug(f"ExitApp raised (expected while SolidWorks shuts down): {ex}")
            app = None
            self.disconnect()
            gc.collect()
            exit_state = wait_for_exit(pid, timeout_s, terminate=True) if pid else "unknown pid"
            if exit_state == "still running":
                return self._result(
                    False,
                    f"SolidWorks (pid {pid}) did not exit within {timeout_s:.0f} s and could not be terminated.",
                    SwErrors.swConnectionError, {"memory_before": before})
            time.sleep(2.0)
            r = self.connect()
            if not r.get("success"):
                return self._result(
                    False, f"Old SolidWorks {exit_state}, but starting a new instance failed: {r.get('message')}",
                    SwErrors.swConnectionError, {"memory_before": before})
            after = memory_status(solidworks_pid(self._sw_app, getattr(self, "_connected_pid", None)))
            return self._result(
                True,
                f"SolidWorks restarted (old pid {pid} {exit_state}; closed {len(titles)} document(s)"
                f"{', discarding unsaved changes in ' + str(len(dirty)) if dirty else ''}). Memory "
                f"{before.get('solidworks_private_mb')} MB -> {after.get('solidworks_private_mb')} MB; "
                f"system free {after.get('system_free_mb')} MB. No document is open now.",
                SwErrors.swSuccess,
                {"old_pid": pid, "exit": exit_state, "closed_documents": titles, "discarded": dirty,
                 "memory_before": before, "memory_after": after},
            )
        except Exception as e:
            logger.error(f"restart_solidworks error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Error: {e}", SwErrors.swGeneralError)

    def manage_configuration(
        self,
        action: str,
        name: Optional[str] = None,
        new_name: Optional[str] = None,
        description: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> Dict:
        """
        Manage document configurations (list, add, activate, rename, delete).

        Args:
            action: 'list', 'add', 'activate', 'rename', 'delete'
            name: Configuration name to add, activate, rename, or delete
            new_name: New configuration name for 'rename' action
            description: Optional description for 'add' action
            comment: Optional comment for 'add' action
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            dtype = self.get_document_type(doc)
            if dtype == "Drawing":
                return self._result(
                    False,
                    "manage_configuration is not supported for Drawing documents (requires Part or Assembly)",
                    SwErrors.swInvalidDocumentType,
                )

            action_clean = (action or "").strip().lower()
            if action_clean not in ("list", "add", "activate", "rename", "delete"):
                return self._result(
                    False,
                    f"Invalid action '{action}'. Valid actions: 'list', 'add', 'activate', 'rename', 'delete'.",
                    SwErrors.swInvalidInput,
                )

            raw_names = get_attr_or_call(doc, "GetConfigurationNames") or ()
            existing_names = list(raw_names) if isinstance(raw_names, (list, tuple)) else [raw_names]

            cm = getattr(doc, "ConfigurationManager", None)
            active_conf = get_attr_or_call(doc, "GetActiveConfiguration")
            if not active_conf and cm:
                active_conf = getattr(cm, "ActiveConfiguration", None)
            active_name = getattr(active_conf, "Name", None) if active_conf else None

            if action_clean == "list":
                configs_info = []
                for cname in existing_names:
                    cobj = doc.GetConfigurationByName(cname)
                    cdesc = get_attr_or_call(cobj, "Description") or "" if cobj else ""
                    configs_info.append({
                        "name": cname,
                        "description": cdesc,
                        "is_active": (cname == active_name),
                    })
                return self._result(
                    True,
                    f"Found {len(existing_names)} configuration(s). Active: '{active_name}'",
                    SwErrors.swSuccess,
                    {
                        "configurations": configs_info,
                        "active_configuration": active_name,
                        "count": len(existing_names),
                    },
                )

            elif action_clean == "add":
                if not name or not name.strip():
                    return self._result(False, "Missing configuration name for 'add' action", SwErrors.swInvalidInput)
                target_name = name.strip()
                if target_name in existing_names:
                    return self._result(
                        False,
                        f"Configuration '{target_name}' already exists in document",
                        SwErrors.swInvalidInput,
                    )

                if not cm:
                    return self._result(False, "ConfigurationManager unavailable", SwErrors.swGeneralError)

                new_conf = cm.AddConfiguration(
                    target_name,
                    comment or "",
                    "",
                    0,
                    "",
                    description or "",
                )
                if new_conf is None:
                    return self._result(False, f"Failed to create configuration '{target_name}'", SwErrors.swFeatureError)

                # Post-condition verification
                updated_names = list(get_attr_or_call(doc, "GetConfigurationNames") or ())
                if target_name not in updated_names:
                    return self._result(
                        False,
                        f"Post-condition failed: configuration '{target_name}' not found after creation",
                        SwErrors.swFeatureError,
                    )

                return self._result(
                    True,
                    f"Successfully created configuration '{target_name}'",
                    SwErrors.swSuccess,
                    {"configuration": target_name, "total_count": len(updated_names)},
                )

            elif action_clean == "activate":
                if not name or not name.strip():
                    return self._result(False, "Missing configuration name for 'activate' action", SwErrors.swInvalidInput)
                target_name = name.strip()
                if target_name not in existing_names:
                    return self._result(
                        False,
                        f"Configuration '{target_name}' does not exist in document",
                        SwErrors.swInvalidInput,
                    )

                curr_active = getattr(cm, "ActiveConfiguration", None) if cm else None
                curr_active_name = getattr(curr_active, "Name", None) if curr_active else None

                if curr_active_name == target_name:
                    return self._result(
                        True,
                        f"Configuration '{target_name}' is already active",
                        SwErrors.swSuccess,
                        {"active_configuration": target_name},
                    )

                ok = doc.ShowConfiguration2(target_name)

                # Post-condition verification
                new_active = getattr(cm, "ActiveConfiguration", None) if cm else None
                new_active_name = new_active.Name if new_active else None
                if new_active_name != target_name and not ok:
                    return self._result(
                        False,
                        f"Post-condition failed: active configuration is '{new_active_name}', expected '{target_name}'",
                        SwErrors.swGeneralError,
                    )

                return self._result(
                    True,
                    f"Successfully activated configuration '{target_name}'",
                    SwErrors.swSuccess,
                    {"active_configuration": target_name},
                )

            elif action_clean == "rename":
                if not name or not name.strip():
                    return self._result(False, "Missing configuration name for 'rename' action", SwErrors.swInvalidInput)
                if not new_name or not new_name.strip():
                    return self._result(False, "Missing new_name for 'rename' action", SwErrors.swInvalidInput)
                old_name = name.strip()
                target_new = new_name.strip()

                if old_name not in existing_names:
                    return self._result(False, f"Configuration '{old_name}' not found", SwErrors.swInvalidInput)
                if target_new in existing_names and target_new != old_name:
                    return self._result(False, f"Configuration '{target_new}' already exists", SwErrors.swInvalidInput)

                cobj = doc.GetConfigurationByName(old_name)
                if not cobj:
                    return self._result(False, f"Could not obtain configuration object for '{old_name}'", SwErrors.swGeneralError)

                cobj.Name = target_new
                get_attr_or_call(doc, "EditRebuild3")

                # Post-condition verification
                updated_names = list(get_attr_or_call(doc, "GetConfigurationNames") or ())
                if target_new not in updated_names or (old_name != target_new and old_name in updated_names):
                    return self._result(
                        False,
                        f"Post-condition failed: configuration '{old_name}' was not renamed to '{target_new}'",
                        SwErrors.swGeneralError,
                    )

                return self._result(
                    True,
                    f"Successfully renamed configuration '{old_name}' to '{target_new}'",
                    SwErrors.swSuccess,
                    {"old_name": old_name, "new_name": target_new},
                )

            elif action_clean == "delete":
                if not name or not name.strip():
                    return self._result(False, "Missing configuration name for 'delete' action", SwErrors.swInvalidInput)
                target_name = name.strip()
                if target_name not in existing_names:
                    return self._result(False, f"Configuration '{target_name}' not found", SwErrors.swInvalidInput)

                # Re-query current active configuration
                active_conf_now = get_attr_or_call(doc, "GetActiveConfiguration")
                if not active_conf_now and cm:
                    active_conf_now = getattr(cm, "ActiveConfiguration", None)
                curr_active = getattr(active_conf_now, "Name", None) if active_conf_now else active_name

                # Refuse deleting active configuration
                if target_name == curr_active:
                    return self._result(
                        False,
                        f"Cannot delete active configuration '{target_name}'. Activate another configuration before deleting.",
                        SwErrors.swInvalidInput,
                    )

                ok = doc.DeleteConfiguration2(target_name)
                if not ok:
                    return self._result(False, f"DeleteConfiguration2 returned failure for '{target_name}'", SwErrors.swGeneralError)

                # Post-condition verification
                updated_names = list(get_attr_or_call(doc, "GetConfigurationNames") or ())
                if target_name in updated_names:
                    return self._result(
                        False,
                        f"Post-condition failed: configuration '{target_name}' still exists after deletion",
                        SwErrors.swGeneralError,
                    )

                return self._result(
                    True,
                    f"Successfully deleted configuration '{target_name}'",
                    SwErrors.swSuccess,
                    {"deleted_configuration": target_name, "remaining_count": len(updated_names)},
                )

        except Exception as e:
            logger.error(f"manage_configuration error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"manage_configuration error: {e}", SwErrors.swGeneralError)

    def _read_cpm_property(self, cpm, prop_name: str):
        """Read both raw expression value and evaluated/resolved value for a custom property."""
        raw_val = ""
        res_val = ""
        # Method 1: ICustomPropertyManager.Get2 with BYREF VARIANTs (standard SW COM API for IDispatch)
        try:
            v1 = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
            v2 = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
            cpm.Get2(str(prop_name), v1, v2)
            v1_val = getattr(v1, "value", None)
            v2_val = getattr(v2, "value", None)
            if v1_val is not None and str(v1_val):
                raw_val = str(v1_val)
            if v2_val is not None and str(v2_val):
                res_val = str(v2_val)
        except Exception:
            pass

        # Method 2: ICustomPropertyManager.Get (direct return value)
        if not raw_val and not res_val:
            try:
                val = cpm.Get(str(prop_name))
                if val is not None and str(val):
                    raw_val = str(val)
                    res_val = str(val)
            except Exception:
                pass

        # Method 3: ICustomPropertyManager.Get5 / Get4
        if not raw_val and not res_val:
            try:
                ret = cpm.Get5(str(prop_name), False, "", "", False)
                if isinstance(ret, (list, tuple)) and len(ret) >= 3:
                    if ret[1] is not None and str(ret[1]):
                        raw_val = str(ret[1])
                    if ret[2] is not None and str(ret[2]):
                        res_val = str(ret[2])
            except Exception:
                pass

        if not res_val:
            res_val = raw_val
        return raw_val, res_val

    def manage_custom_properties(
        self,
        action: str,
        name: Optional[str] = None,
        value: Optional[Any] = None,
        property_type: Optional[str] = "text",
        configuration: Optional[str] = "",
    ) -> Dict:
        """
        Manage document custom properties (list, get, set, delete) at file or configuration level.

        Args:
            action: 'list', 'get', 'set', 'delete'
            name: Property name
            value: Property value (for 'set')
            property_type: 'text', 'number', 'date', 'yesno' (default 'text')
            configuration: Configuration name for configuration-specific property, or empty string/None for file-level
        """
        try:
            doc, err = self.get_active_doc()
            if err:
                return err

            dtype = self.get_document_type(doc)
            cfg_target = (configuration or "").strip()
            if cfg_target and dtype == "Drawing":
                return self._result(
                    False,
                    "Drawing documents do not support configuration-specific properties (leave configuration empty)",
                    SwErrors.swInvalidDocumentType,
                )

            action_clean = (action or "").strip().lower()
            if action_clean not in ("list", "get", "set", "delete"):
                return self._result(
                    False,
                    f"Invalid action '{action}'. Valid actions: 'list', 'get', 'set', 'delete'.",
                    SwErrors.swInvalidInput,
                )

            ext = getattr(doc, "Extension", None)
            if not ext:
                return self._result(False, "Document Extension interface unavailable", SwErrors.swGeneralError)

            cpm = ext.CustomPropertyManager(cfg_target)
            if not cpm:
                return self._result(
                    False,
                    f"CustomPropertyManager unavailable for configuration '{cfg_target}'",
                    SwErrors.swInvalidInput,
                )

            raw_names = get_attr_or_call(cpm, "GetNames")
            if not raw_names:
                existing_names = []
            else:
                existing_names = list(raw_names) if isinstance(raw_names, (list, tuple)) else [raw_names]

            if action_clean == "list":
                props = []
                for pname in existing_names:
                    raw_val, res_val = self._read_cpm_property(cpm, str(pname))
                    try:
                        ptype = get_attr_or_call(cpm, "GetType2", pname)
                    except Exception:
                        ptype = 30
                    props.append({
                        "name": str(pname),
                        "value": raw_val,
                        "resolved_value": res_val,
                        "type": ptype,
                    })
                return self._result(
                    True,
                    f"Retrieved {len(props)} custom property(ies) (scope: {'file-level' if not cfg_target else f'config [{cfg_target}]'})",
                    SwErrors.swSuccess,
                    {
                        "properties": props,
                        "count": len(props),
                        "configuration": cfg_target,
                    },
                )

            elif action_clean == "get":
                if not name or not name.strip():
                    return self._result(False, "Missing property name for 'get' action", SwErrors.swInvalidInput)
                prop_name = name.strip()
                if prop_name not in existing_names:
                    return self._result(
                        False,
                        f"Custom property '{prop_name}' not found",
                        SwErrors.swInvalidInput,
                    )

                raw_val, res_val = self._read_cpm_property(cpm, prop_name)
                ptype = get_attr_or_call(cpm, "GetType2", prop_name)

                return self._result(
                    True,
                    f"Retrieved property '{prop_name}': '{res_val}'",
                    SwErrors.swSuccess,
                    {
                        "name": prop_name,
                        "value": raw_val,
                        "resolved_value": res_val,
                        "type": ptype,
                        "configuration": cfg_target,
                    },
                )

            elif action_clean == "set":
                if not name or not name.strip():
                    return self._result(False, "Missing property name for 'set' action", SwErrors.swInvalidInput)
                if value is None:
                    return self._result(False, "Missing value for 'set' action", SwErrors.swInvalidInput)
                prop_name = name.strip()

                type_map = {
                    "text": 30,      # swCustomInfoText
                    "string": 30,
                    "number": 3 if isinstance(value, int) else 5,  # swCustomInfoNumber / swCustomInfoDouble
                    "int": 3,
                    "double": 5,
                    "date": 64,      # swCustomInfoDate
                    "yesno": 11,     # swCustomInfoYesOrNo
                    "boolean": 11,
                }
                ptype_int = type_map.get(str(property_type).lower(), 30)
                if ptype_int == 11:
                    val_str = "Yes" if str(value).strip().lower() in ("true", "1", "yes") else "No"
                else:
                    val_str = str(value)
                if ptype_int in (3, 5) and not val_str.startswith("$PRP:") and not val_str.startswith('"'):
                    try:
                        float(val_str)
                    except ValueError:
                        return self._result(
                            False,
                            f"Custom property '{prop_name}': '{val_str}' is not a number (property_type='number').",
                            SwErrors.swInvalidParameter,
                        )

                if prop_name in existing_names:
                    cpm.Set2(prop_name, val_str)
                else:
                    added = False
                    try:
                        cpm.Add2(prop_name, ptype_int, val_str)
                        added = True
                    except Exception:
                        pass
                    if not added:
                        try:
                            cpm.Add3(prop_name, ptype_int, val_str, 2)
                        except Exception:
                            pass

                # Post-condition verification. The property must actually exist first: the
                # ByRef read-back echoes the requested value for a property SolidWorks rejected
                # (e.g. "abc" as a number), which used to be reported as success.
                current_names = list(get_attr_or_call(cpm, "GetNames") or ())
                if prop_name not in current_names:
                    hint = " (value is not a valid number)" if ptype_int in (3, 5) else ""
                    return self._result(
                        False,
                        f"Custom property '{prop_name}' was not created{hint}.",
                        SwErrors.swGeneralError,
                    )
                raw_read, res_read = self._read_cpm_property(cpm, prop_name)
                matched = False
                if raw_read is not None or res_read is not None:
                    if ptype_int in (3, 5):  # number / double
                        try:
                            matched = abs(float(raw_read) - float(val_str)) < 1e-4 or abs(float(res_read) - float(val_str)) < 1e-4
                        except Exception:
                            matched = (raw_read == val_str or res_read == val_str)
                    elif ptype_int == 11:    # yesno
                        matched = (str(raw_read).strip().lower() == val_str.strip().lower() or str(res_read).strip().lower() == val_str.strip().lower())
                    elif val_str.startswith("$PRP:"):
                        matched = (raw_read == val_str or res_read == val_str)
                    else:
                        matched = (raw_read == val_str or res_read == val_str or str(raw_read).strip() == str(val_str).strip() or str(res_read).strip() == str(val_str).strip())

                if not matched:
                    return self._result(
                        False,
                        f"Post-condition failed: Custom property '{prop_name}' read-back '{raw_read}' does not match set value '{val_str}'",
                        SwErrors.swGeneralError,
                    )

                return self._result(
                    True,
                    f"Successfully set custom property '{prop_name}' = '{val_str}'",
                    SwErrors.swSuccess,
                    {
                        "name": prop_name,
                        "value": raw_read or val_str,
                        "resolved_value": res_read,
                        "configuration": cfg_target,
                    },
                )

            elif action_clean == "delete":
                if not name or not name.strip():
                    return self._result(False, "Missing property name for 'delete' action", SwErrors.swInvalidInput)
                prop_name = name.strip()
                if prop_name not in existing_names:
                    return self._result(False, f"Custom property '{prop_name}' not found", SwErrors.swInvalidInput)

                del_code = cpm.Delete2(prop_name)
                # Post-condition verification
                updated_names = list(get_attr_or_call(cpm, "GetNames") or ())
                if prop_name in updated_names:
                    return self._result(
                        False,
                        f"Post-condition failed: Custom property '{prop_name}' still present after deletion",
                        SwErrors.swGeneralError,
                    )

                return self._result(
                    True,
                    f"Successfully deleted custom property '{prop_name}'",
                    SwErrors.swSuccess,
                    {"deleted_property": prop_name, "configuration": cfg_target},
                )

        except Exception as e:
            logger.error(f"manage_custom_properties error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"manage_custom_properties error: {e}", SwErrors.swGeneralError)

