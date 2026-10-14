"""
Unit tests for the interactive consent gate on execute_python.
"""
import os
import pytest
from solidworks_mcp.constants import SwErrors
import solidworks_mcp.server as srv


def test_execute_python_default_refusal(monkeypatch):
    """When locked, execute_python must refuse execution and return a consent prompt."""
    monkeypatch.delenv("SW_MCP_ALLOW_EXEC", raising=False)
    monkeypatch.delenv("SWMCP_ENABLE_EXECUTE_PYTHON", raising=False)
    srv._session_exec_unlocked = False

    result = srv._execute_python_fixed("print('test')", user_confirmed_risks=False)
    assert result["success"] is False
    assert result["error_code"] == int(SwErrors.swInvalidCall)
    assert result.get("security_gate") == "consent_required"
    assert "BENEFITS" in result["message"]
    assert "RISKS" in result["message"]
    assert "user_confirmed_risks" in result["message"]


def test_execute_python_user_confirmed_risks_unlocks(monkeypatch):
    """Passing user_confirmed_risks=True unlocks execution and updates session state."""
    monkeypatch.delenv("SW_MCP_ALLOW_EXEC", raising=False)
    monkeypatch.delenv("SWMCP_ENABLE_EXECUTE_PYTHON", raising=False)
    srv._session_exec_unlocked = False
    cfg = srv.get_config()
    monkeypatch.setattr(cfg, "auto_connect", False)
    monkeypatch.setattr(type(srv.sw_automation), "is_connected", property(lambda self: False))

    # When unlocked, it moves past the consent gate (returns connection refusal when auto_connect=False)
    result = srv._execute_python_fixed("print('test')", user_confirmed_risks=True)
    assert result.get("security_gate") != "consent_required"
    assert result["error_code"] == int(SwErrors.swConnectionError)
    assert srv._session_exec_unlocked is True


def test_execute_python_env_var_unlocks(monkeypatch):
    """Setting SW_MCP_ALLOW_EXEC=1 bypasses the consent gate."""
    monkeypatch.setenv("SW_MCP_ALLOW_EXEC", "1")
    srv._session_exec_unlocked = False
    cfg = srv.get_config()
    monkeypatch.setattr(cfg, "auto_connect", False)
    monkeypatch.setattr(type(srv.sw_automation), "is_connected", property(lambda self: False))

    result = srv._execute_python_fixed("print('test')", user_confirmed_risks=False)
    assert result.get("security_gate") != "consent_required"
    assert result["error_code"] == int(SwErrors.swConnectionError)
