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
