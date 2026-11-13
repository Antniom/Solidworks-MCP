"""
Unit test for F2: scans codebase with AST for SwErrors.<name>
and fails if any referenced name is not defined in constants.SwErrors.
"""

import ast
import os
import unittest
from solidworks_mcp.constants import SwErrors


class TestSwErrorsAST(unittest.TestCase):
    def test_all_referenced_sw_errors_are_defined(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        defined_errors = set(SwErrors.__members__.keys())
        
        referenced_errors = {}
        
        # Scan solidworks_mcp codebase
        target_dir = os.path.join(repo_root, "solidworks_mcp")
        for root, dirs, files in os.walk(target_dir):
            if "__pycache__" in root:
                continue
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(root, f)
                    with open(path, "r", encoding="utf-8", errors="ignore") as src:
                        try:
                            tree = ast.parse(src.read(), filename=path)
                        except Exception as e:
                            self.fail(f"AST parsing failed on {path}: {e}")
                            
                        for node in ast.walk(tree):
                            if (
                                isinstance(node, ast.Attribute)
                                and isinstance(node.value, ast.Name)
                                and node.value.id == "SwErrors"
                                and not node.attr.startswith("__")
                            ):
                                rel_path = os.path.relpath(path, repo_root)
                                referenced_errors.setdefault(node.attr, []).append(f"{rel_path}:{node.lineno}")

        missing = {name: locations for name, locations in referenced_errors.items() if name not in defined_errors}
        
        self.assertEqual(
            missing,
            {},
            f"The following SwErrors are referenced in the codebase but undefined in SwErrors: {missing}"
        )


if __name__ == "__main__":
    unittest.main()
