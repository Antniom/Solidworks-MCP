"""
Unit tests for COM compatibility helpers (F1)
"""

import pytest
from solidworks_mcp.core.com_compat import (
    get_attr_or_call,
    byref_int,
    byref_bool,
    byref_double,
    null_dispatch,
)


class DummyProp:
    def __init__(self, val):
        self.val = val

    @property
    def PropValue(self):
        return self.val

    def MethodValue(self, multiplier=1):
        return self.val * multiplier

    # Simulates pywin32 returning a plain string attribute for a COM method name
    StringProp = "ProfileFeature"


def test_get_attr_or_call_property():
    d = DummyProp(42)
    assert get_attr_or_call(d, "PropValue") == 42


def test_get_attr_or_call_method():
    d = DummyProp(42)
    assert get_attr_or_call(d, "MethodValue") == 42
    assert get_attr_or_call(d, "MethodValue", 2) == 84


def test_get_attr_or_call_string_attribute():
    d = DummyProp(42)
    assert get_attr_or_call(d, "StringProp") == "ProfileFeature"


def test_byref_variants():
    bi = byref_int(10)
    assert getattr(bi, "value", None) == 10

    bb = byref_bool(True)
    assert getattr(bb, "value", None) is True

    bd = byref_double(3.14)
    assert abs(getattr(bd, "value", 0.0) - 3.14) < 1e-5
