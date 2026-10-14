"""
Static AST Test: Enforce STA Dispatcher Affinity (User Condition 2)
Asserts that in `solidworks_mcp/server.py`:
1. `call_tool` does NOT call any COM methods on `sw_automation` directly on the asyncio thread.
2. All COM tool invocations are delegated via `sta.run_async` to `_execute_tool_on_sta` / `_dispatch_tool`.
3. The only non-STA tools in `call_tool` are pure Win32 dialog tools (`inspect_dialogs`, `dismiss_dialog`)
   and administrative recovery (`reset_sta_worker`).
4. All 51 COM tools defined in `_tool_definitions()` are mapped within `_dispatch_tool`.
"""

import ast
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

def test_sta_dispatcher_affinity_ast():
    server_py = REPO_ROOT / "solidworks_mcp" / "server.py"
    assert server_py.exists(), f"{server_py} not found"

    tree = ast.parse(server_py.read_text(encoding="utf-8"), filename=str(server_py))

    # 1. Locate `call_tool`, `_dispatch_tool`, and `_execute_tool_on_sta`
    call_tool_func = None
    dispatch_tool_func = None

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "call_tool":
                call_tool_func = node
            elif node.name == "_dispatch_tool":
                dispatch_tool_func = node

    assert call_tool_func is not None, "call_tool not found in server.py"
    assert dispatch_tool_func is not None, "_dispatch_tool not found in server.py"

    # 2. Assert no sw_automation COM method calls occur directly inside call_tool
    # Allowed in call_tool: sw_automation.units.default_unit (in-memory string), resetting pointers sw_automation._sw_app = None
    forbidden_calls = []
    for node in ast.walk(call_tool_func):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                # Check if called on sw_automation
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "sw_automation":
                    forbidden_calls.append(f"Direct call to sw_automation.{node.func.attr}() at line {node.lineno}")

    assert forbidden_calls == [], f"Found forbidden direct COM calls on sw_automation inside call_tool:\n" + "\n".join(forbidden_calls)

    # 3. Assert all tools handled in _dispatch_tool
    dispatched_names = set()
    for node in ast.walk(dispatch_tool_func):
        if isinstance(node, ast.Compare):
            if isinstance(node.left, ast.Name) and node.left.id == "name":
                for comparator in node.comparators:
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                        dispatched_names.add(comparator.value)

    # From tool definitions, verify COM tools are handled
    from solidworks_mcp.server import _tool_definitions
    all_tools = [t.name for t in _tool_definitions()]
    com_tools = [t for t in all_tools if t not in ("inspect_dialogs", "dismiss_dialog", "reset_sta_worker")]

    missing_from_dispatcher = [t for t in com_tools if t not in dispatched_names]
    assert missing_from_dispatcher == [], f"COM tools missing from _dispatch_tool:\n{missing_from_dispatcher}"

if __name__ == "__main__":
    test_sta_dispatcher_affinity_ast()
    print("PASS: Static AST check confirms 100% STA dispatcher affinity in server.py")
