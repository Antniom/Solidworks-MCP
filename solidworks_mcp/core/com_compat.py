"""
COM Compatibility and pywin32 Helpers
--------------------------------------
Provides unified helpers for pywin32 COM interactions with SolidWorks:
- get_attr_or_call: Safely accesses members whether exposed as properties or methods
- ByRef VARIANT helpers for COM out parameters (GetErrorCode2, Save3, etc.)
- Null dispatch wrappers
"""

import logging
from typing import Any, Optional

try:
    import win32com.client
    import pythoncom
    HAS_PYWIN32 = True

    # Enable native Python numeric coercion on VARIANT instances so early-binding
    # wrappers (like _ApplyTypes_) and int(variant) succeed seamlessly.
    if not hasattr(win32com.client.VARIANT, "__int__"):
        win32com.client.VARIANT.__int__ = lambda self: int(self.value) if getattr(self, "value", None) is not None else 0
    if not hasattr(win32com.client.VARIANT, "__float__"):
        win32com.client.VARIANT.__float__ = lambda self: float(self.value) if getattr(self, "value", None) is not None else 0.0
    if not hasattr(win32com.client.VARIANT, "__bool__"):
        win32com.client.VARIANT.__bool__ = lambda self: bool(self.value) if getattr(self, "value", None) is not None else False
except ImportError:
    HAS_PYWIN32 = False

logger = logging.getLogger("SolidWorksMCP.COMCompat")


def get_object_member(obj: Any, name: str) -> Any:
    """
    Read a member that returns another COM object (FirstFeature, GetNextFeature, ...).

    pywin32 exposes such members as a property on some builds and as a method on others.
    The returned COM object is itself callable, so `callable()` cannot tell the two apart;
    only a genuine bound method is invoked here.
    """
    import inspect
    try:
        value = getattr(obj, name)
    except Exception:
        return None
    if inspect.ismethod(value) or inspect.isfunction(value) or inspect.isbuiltin(value):
        return value()
    return value


def get_attr_or_call(obj: Any, name: str, *args, **kwargs) -> Any:
    """
    Safely access an attribute or invoke a method on a COM object.
    
    Handles pywin32 COM member dispatch differences where a COM member
    may be exposed as a property (returning a string/int/bool directly) or
    as a callable method. Specifically resolves 'str' object is not callable
    exceptions on methods like GetTypeName2.
    
    Args:
        obj: COM object or Python object
        name: Member name to access
        *args: Positional arguments if called as a method
        **kwargs: Keyword arguments if called as a method
        
    Returns:
        The property value or method return value
    """
    try:
        attr = getattr(obj, name)
    except Exception as e:
        if not args and not kwargs:
            return None
        raise AttributeError(f"Object {obj} has no attribute '{name}': {e}") from e

    if callable(attr):
        try:
            return attr(*args, **kwargs)
        except TypeError as te:
            # If no arguments were passed and attr was misidentified as callable or raised not callable
            err_msg = str(te).lower()
            if not args and not kwargs and ("not callable" in err_msg or "takes 0 positional arguments" in err_msg):
                return attr
            raise
        except Exception:
            # On COM objects, accessing a property can return a callable CDispatch wrapper whose __call__ fails with DISP_E_MEMBERNOTFOUND
            if not args and not kwargs:
                return attr
            raise
    else:
        if args or kwargs:
            raise TypeError(f"Property '{name}' on {obj} is not callable with arguments")
        return attr


def transform_point(arr: Any, p: Any) -> list:
    """
    Apply a SolidWorks MathTransform (its 13/16-element ArrayData) to a 3D point.
    Row-vector convention: p' = scale * (p . R) + T, R = arr[0:9], T = arr[9:12], scale = arr[12].
    Done in Python because IMathUtility.CreatePoint / CreateTransform raise
    DISP_E_MEMBERNOTFOUND under pywin32 late binding.
    """
    a = [float(v) for v in arr]
    x, y, z = float(p[0]), float(p[1]), float(p[2])
    s = a[12] if len(a) > 12 and a[12] else 1.0
    return [
        (x * a[0] + y * a[3] + z * a[6]) * s + a[9],
        (x * a[1] + y * a[4] + z * a[7]) * s + a[10],
        (x * a[2] + y * a[5] + z * a[8]) * s + a[11],
    ]


def byref_int(initial: int = 0) -> Any:
    """
    Create a ByRef VT_I4 VARIANT for COM [out] integer parameters.
    Used for error codes, warning flags, options.
    """
    if not HAS_PYWIN32:
        return initial
    return win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, int(initial))


def byref_bool(initial: bool = False) -> Any:
    """
    Create a ByRef VT_BOOL VARIANT for COM [out] boolean parameters.
    Used for IFeature::GetErrorCode2 IsWarning out parameter.
    """
    if not HAS_PYWIN32:
        return initial
    return win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, bool(initial))


def byref_double(initial: float = 0.0) -> Any:
    """
    Create a ByRef VT_R8 VARIANT for COM [out] double parameters.
    """
    if not HAS_PYWIN32:
        return initial
    return win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_R8, float(initial))


def null_dispatch() -> Any:
    """
    Create a wrapped null IDispatch VARIANT.
    Prevents Type Mismatch errors when SolidWorks COM expects an empty callout/dispatch.
    """
    if not HAS_PYWIN32:
        return None
    return win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)

def tight_body_box(body):
    """
    Exact bounding box [xmin, ymin, zmin, xmax, ymax, zmax] (m) of a solid body from
    IBody2.GetExtremePoint in the six axis directions. IBody2.GetBodyBox is a loose box that can
    be more than a millimetre too big on bodies with curved faces (MCP-034); it is the fallback.
    """
    try:
        ext = []
        for axis in range(3):
            for sign in (-1.0, 1.0):
                d = [0.0, 0.0, 0.0]
                d[axis] = sign
                r = body.GetExtremePoint(d[0], d[1], d[2])
                if not r or not r[0]:
                    raise ValueError("no extreme point")
                ext.append(float(r[1 + axis]))
        return [ext[0], ext[2], ext[4], ext[1], ext[3], ext[5]]
    except Exception:
        try:
            bx = get_attr_or_call(body, "GetBodyBox")
            return [float(v) for v in bx[:6]] if bx and len(bx) >= 6 else None
        except Exception:
            return None

def _save_selection(doc):
    """[(object, mark)] of the current selection, so a measurement can put it back."""
    saved = []
    try:
        sm = doc.SelectionManager
        for i in range(1, int(sm.GetSelectedObjectCount2(-1)) + 1):
            try:
                saved.append((sm.GetSelectedObject6(i, -1), int(sm.GetSelectedObjectMark(i))))
            except Exception:
                pass
    except Exception:
        pass
    return saved


def _restore_selection(doc, saved):
    """Re-select what _save_selection recorded (with its marks); best effort."""
    try:
        doc.ClearSelection2(True)
    except Exception:
        return
    for obj, mark in saved:
        if obj is None:
            continue
        try:
            if not obj.Select2(True, mark):
                raise ValueError
        except Exception:
            try:
                obj.Select4(True, None)
            except Exception:
                pass


def _measure_items(doc):
    """What 'the whole document' is for IMassProperty2: every solid body, or every top-level component."""
    try:
        if int(get_attr_or_call(doc, "GetType")) == 2:  # assembly
            return [c for c in (doc.GetComponents(True) or ()) if not get_attr_or_call(c, "IsSuppressed")]
        return list(doc.GetBodies2(0, False) or ())
    except Exception:
        return []


def _mass_property2(doc, items):
    """IMassProperty2 at accuracy 2 over `items`, recalculated. Restores the document selection,
    which SelectedItems changes (and which, left alone, limits the measurement: MCP-042)."""
    import pythoncom
    import win32com.client
    saved = _save_selection(doc)
    try:
        mp = get_attr_or_call(doc.Extension, "CreateMassProperty2")
        mp.AccuracyLevel = 2
        mp.SelectedItems = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, list(items))
        get_attr_or_call(mp, "Recalculate")  # without it the values belong to the previous item set
        return mp
    finally:
        _restore_selection(doc, saved)


def accurate_mass_properties(doc):
    """
    Document mass properties at SolidWorks' highest accuracy over ALL bodies / components:
    [cx, cy, cz, volume, area, mass] in SI units, or None.
    - The default accuracy is off by about 4e-6 of the volume (MCP-037).
    - GetMassProperties2 returns cached values (MCP-040).
    - IMassProperty2 without explicit items measures only the current selection (MCP-042).
    """
    try:
        items = _measure_items(doc)
        if items:
            mp = _mass_property2(doc, items)
            vol = float(get_attr_or_call(mp, "Volume"))
            if vol > 0:
                com = list(get_attr_or_call(mp, "CenterOfMass") or (0.0, 0.0, 0.0))[:3]
                area = float(get_attr_or_call(mp, "SurfaceArea") or 0.0)
                mass = float(get_attr_or_call(mp, "Mass") or 0.0)
                return [float(com[0]), float(com[1]), float(com[2]), vol, area, mass]
    except Exception:
        pass
    try:
        import pythoncom
        import win32com.client
        status = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        r = doc.Extension.GetMassProperties2(2, status, False)
        if r and len(r) >= 6 and float(r[3]) > 0:
            return [float(v) for v in r]
    except Exception:
        pass
    return None


def accurate_body_volume_m3(doc, body):
    """
    Volume (m3) of one solid body at the highest accuracy (IMassProperty2 over that body; the
    selection is restored). IBody2.GetMassProperties has no accuracy setting and read 0.12 % high on
    a wire shelf (MCP-040); it is only the fallback.
    """
    try:
        v = float(get_attr_or_call(_mass_property2(doc, [body]), "Volume"))
        if v > 0:
            return v
    except Exception:
        pass
    try:
        return float(body.GetMassProperties(1.0)[3])
    except Exception:
        return None
