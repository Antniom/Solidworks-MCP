"""
Unit / Static AST Test: Leak Prevention (Requirement 1 & Requirement 2.a)
Asserts that NO file in solidworks_mcp/, scripts/, or tests/ initiates SolidWorks via:
- win32com.client.Dispatch("SldWorks.Application")
- win32com.client.DispatchEx
- gencache.EnsureDispatch
- win32com.client.gencache.EnsureDispatch
- CreateObject("SldWorks.Application")
- pythoncom.CoCreateInstance
- os.startfile(SLDWORKS.exe)
- subprocess.Popen / subprocess.run / subprocess.call for SLDWORKS.exe

EXCEPT:
1. The sole blessed factory function `launch_solidworks()` in `solidworks_mcp/automation/base.py`.
2. The opt-in unattachable orphan generation tests: `scripts/test_unattachable_orphans.py`
   and `tests/test_orphan_refusal.py` (explicitly whitelisted by orphan marker).
"""

import ast
import sys
from pathlib import Path
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

LAUNCH_FUNCTIONS = {
    "dispatch",
    "dispatchex",
    "ensuredispatch",
    "createobject",
    "cocreateinstance",
    "startfile",
    "popen",
    "run",
    "call",
}

def find_dispatch_violations():
    violations = []
    target_dirs = [
        PROJECT_ROOT / "solidworks_mcp",
        PROJECT_ROOT / "scripts",
        PROJECT_ROOT / "tests",
    ]

    for d in target_dirs:
        for py_file in d.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))
                lines = content.splitlines()
            except Exception as e:
                violations.append(f"Failed to parse {py_file}: {e}")
                continue

            rel_path = py_file.relative_to(PROJECT_ROOT).as_posix()

            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # Extract function name
                    func_name = ""
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr

                    if func_name.lower() in LAUNCH_FUNCTIONS:
                        line_text = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
                        # Check if arguments or the call line mention SldWorks / SLDWORKS.exe
                        arg_mentions_sw = False

                        # 1. Inspect direct AST arguments
                        for arg in node.args:
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                if "sldworks" in arg.value.lower():
                                    arg_mentions_sw = True
                            elif isinstance(arg, ast.List):
                                for elt in arg.elts:
                                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                        if "sldworks" in elt.value.lower():
                                            arg_mentions_sw = True

                        # 2. Inspect line text for launch targets (e.g., target_exe or "SldWorks.Application")
                        if "sldworks" in line_text.lower():
                            arg_mentions_sw = True

                        if arg_mentions_sw:
                            is_blessed = False

                            # Check if inside launch_solidworks() in solidworks_mcp/automation/base.py
                            if rel_path == "solidworks_mcp/automation/base.py":
                                for parent in ast.walk(tree):
                                    if isinstance(parent, ast.FunctionDef) and parent.name == "launch_solidworks":
                                        for child in ast.walk(parent):
                                            if child is node:
                                                is_blessed = True
                                                break

                            # Check if inside whitelisted opt-in orphan test scripts
                            elif rel_path in ("scripts/test_unattachable_orphans.py", "tests/test_orphan_refusal.py"):
                                is_blessed = True

                            if not is_blessed:
                                violations.append(
                                    f"Forbidden SolidWorks launch call in {rel_path}:{node.lineno}: "
                                    f"'{line_text.strip()}' (outside launch_solidworks())"
                                )

    return violations

def test_no_direct_dispatch_calls():
    """Fail if any file initiates SolidWorks outside launch_solidworks()."""
    violations = find_dispatch_violations()
    assert violations == [], f"Found forbidden Direct Launch/Dispatch calls:\n" + "\n".join(violations)

if __name__ == "__main__":
    v = find_dispatch_violations()
    if v:
        print("FAIL: Violations found:")
        for x in v:
            print(" ", x)
        sys.exit(1)
    else:
        print("PASS: 0 forbidden SolidWorks launch calls found across solidworks_mcp/, scripts/, and tests/.")
