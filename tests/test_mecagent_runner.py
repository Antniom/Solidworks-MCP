"""
Integration tests for the MecAgent out-of-process .NET 10 runner bridge
(`solidworks_mcp/core/mecagent_runner.py`) and the 8 MecAgent MCP tools
registered in `solidworks_mcp/server.py`.
"""

import pytest
from solidworks_mcp.core.mecagent_runner import MecAgentRunnerBridge

import os

# The runner is a Windows executable; it ships in solidworks_mcp/bin/mecagent_runner.
needs_runner = pytest.mark.skipif(os.name != "nt", reason="the .NET runner only runs on Windows")


def test_runner_is_bundled():
    exe = MecAgentRunnerBridge._resolve_runner_exe()
    assert exe.replace("\\", "/").endswith("solidworks_mcp/bin/mecagent_runner/MecAgent.SolidWorks.Runner.exe")

from solidworks_mcp.server import _tool_definitions, _validate_tool_arguments, _dispatch_tool


# Tools whose runner-backed modes reach the .NET runner.
EXPECTED_MECAGENT_TOOLS = {
    "execute_code",
    "capture_viewport",
    "get_feature_tree",
    "select",
    "inspect_topology",
    "find_related_files",
    "modify_feature_by_path",
}


def test_mecagent_tools_registered_and_schemas_valid():
    """Verify the runner-backed tools are registered in _tool_definitions() with valid schemas."""
    tools = {t.name: t for t in _tool_definitions()}
    missing = EXPECTED_MECAGENT_TOOLS - set(tools.keys())
    assert not missing, f"Missing MecAgent tools: {missing}"

    assert _validate_tool_arguments("execute_code", {"language": "csharp", "code": "1 + 1"}) is None
    assert _validate_tool_arguments("execute_code", {"language": "csharp"}) is not None
    assert _validate_tool_arguments("execute_code", {"code": "1 + 1"}) is not None

    assert _validate_tool_arguments("get_feature_tree", {"detail": "context", "depth": 0}) is None
    assert _validate_tool_arguments("get_feature_tree", {"detail": "context", "depth": 5}) is None
    assert _validate_tool_arguments("get_feature_tree", {"detail": "context", "depth": 99}) is not None

    assert _validate_tool_arguments("select", {"by": "path", "path": "Boss-Extrude1/Face[0]"}) is None
    assert _validate_tool_arguments("inspect_topology", {"path": "/"}) is None
    assert _validate_tool_arguments("find_related_files", {"max_drawings_probed": 100}) is None


@pytest.fixture(scope="module")
def runner():
    """Shared out-of-process .NET 10 runner instance for the test module."""
    bridge = MecAgentRunnerBridge()
    yield bridge
    bridge.shutdown()


@needs_runner
def test_runner_startup_and_ready_handshake(runner: MecAgentRunnerBridge):
    """Verify MecAgent.SolidWorks.Runner.exe starts over anonymous pipes and emits ReadyMessage."""
    ready = runner.ensure_started(timeout=30.0)
    assert ready.get("type") == "ready"
    assert ready.get("pid", 0) > 0
    assert ready.get("referenceCount", 0) >= 50
    assert "33.5" in str(ready.get("interopVersion", ""))


@needs_runner
def test_stateful_csharp_repl_and_final_answer(runner: MecAgentRunnerBridge):
    """Verify Roslyn CellSession persists variables across cells and supports FinalAnswer / final_answer."""
    c1 = runner.execute_csharp('int persistedDim = 35; Print("dim=" + persistedDim); persistedDim')
    assert c1["success"] is True
    assert c1["returnValue"] == 35
    assert "dim=35" in c1["stdout"]
    assert c1["effect"]["readOnly"] is True

    c2 = runner.execute_csharp('final_answer("Computed: " + (persistedDim * 2)); persistedDim * 2')
    assert c2["success"] is True
    assert c2["returnValue"] == 70
    assert c2["finalAnswer"] == "Computed: 70"


@needs_runner
def test_cast_repair_and_static_effect_classifier(runner: MecAgentRunnerBridge):
    """
    Verify CastRepair automatically fixes CS1061 missing COM interface casts
    and CellEffectAnalyzer classifies ReadOnly vs Mutating calls against sw-api-effects.json.
    """
    # 1. IModelDoc2 -> ((IPartDoc)doc) automatic CS1061 repair + ReadOnly classification
    c_ro = runner.execute_csharp("IModelDoc2 doc = null; var bodies = doc?.GetBodies2(0, true); bodies")
    assert c_ro["success"] is True
    assert c_ro["effect"]["readOnly"] is True
    assert len(c_ro["repairs"]) == 1
    assert "IPartDoc" in c_ro["repairs"][0]["after"]

    # 2. Mutating method (IModelDoc2.EditRebuild3) blocked when read_only_only=True
    c_mut = runner.execute_csharp("IModelDoc2 doc = null; doc?.EditRebuild3();", read_only_only=True)
    assert c_mut["success"] is False
    assert c_mut["stage"] == "PolicyBlocked"
    assert c_mut["effect"]["readOnly"] is False
    symbols = [f["symbol"] for f in c_mut["effect"]["findings"]]
    assert any("EditRebuild3" in s for s in symbols)


@needs_runner
def test_execute_macro_and_sdk_bundle(runner: MecAgentRunnerBridge):
    """Verify DynamicClass.Execute macro execution and cached McpSdkBundle invocation."""
    macro_code = """
    public class DynamicClass
    {
        public static object Execute()
        {
            return new { status = "macro_ok", sum = 10 + 20 };
        }
    }
    """
    m_res = runner.execute_macro(macro_code, read_only_only=True)
    assert m_res["success"] is True
    assert m_res["effect"]["readOnly"] is True
    assert m_res["returnValue"] == {"status": "macro_ok", "sum": 30}

    # Invoke cached SDK bundle (returns either live context or structured no-connection response)
    ctx = runner.invoke_sdk_method("getContextFast", {"depth": 1})
    assert isinstance(ctx, dict)
    assert "success" in ctx


@needs_runner
def test_server_dispatch_sw_execute_csharp(monkeypatch):
    """Verify _dispatch_tool routes C# execution cleanly (code execution enabled)."""
    monkeypatch.setenv("SW_MCP_ALLOW_EXEC", "1")
    res = _dispatch_tool(
        "execute_code",
        {"language": "csharp", "code": "Math.Sqrt(144)", "read_only": True},
    )
    assert res["success"] is True
    assert res["data"]["returnValue"] == 12.0


def test_missing_runner_is_reported_clearly(monkeypatch):
    """If the runner files are gone, runner-backed tools fail with guidance instead of a traceback."""
    from solidworks_mcp import server as srv
    from solidworks_mcp.core import mecagent_runner

    bridge = mecagent_runner.MecAgentRunnerBridge()
    bridge._runner_exe = ""
    monkeypatch.setattr(srv, "get_mecagent_runner", lambda: bridge)
    monkeypatch.setattr(srv.sw_automation, "_result",
                        lambda ok, msg, code, data=None: {"success": ok, "message": msg}, raising=False)

    # Looked up on the module: an earlier reload_api test may have re-created the class.
    with pytest.raises(mecagent_runner.RunnerNotInstalledError):
        bridge.ensure_started()
    res = srv._dispatch_tool("get_feature_tree", {"detail": "parametric"})
    assert res["success"] is False and "MECAGENT_RUNNER_PATH" in res["message"]
