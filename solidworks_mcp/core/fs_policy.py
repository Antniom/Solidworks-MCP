"""
Safe Filesystem Policy
----------------------
Enforces security boundaries, path canonicalization, and extension checks
for all file operations (open, save, export, screenshot).
"""

import os
import tempfile
from pathlib import Path
from typing import List, Optional, Set, Tuple


class FileSystemPolicy:
    """
    Enforces safe filesystem access boundaries for SolidWorks operations.
    Prevents directory traversal, unauthorized disk access, and invalid file types.
    """

    ALLOWED_CAD_EXTENSIONS: Set[str] = {
        ".sldprt",
        ".sldasm",
        ".slddrw",
        ".prtdot",
        ".asmdot",
        ".drwdot",
        ".step",
        ".stp",
        ".iges",
        ".igs",
        ".stl",
        ".x_t",
        ".x_b",
        ".dxf",
        ".dwg",
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
    }

    def __init__(self, allowed_roots: Optional[List[str]] = None):
        roots = allowed_roots or []
        # Include current working directory by default
        roots.append(os.getcwd())
        # Include user profile / Documents / Desktop
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            roots.append(user_profile)
        # Include temp folder
        roots.append(tempfile.gettempdir())
        # Include test directory if configured via environment or tempfile
        env_test_dir = os.environ.get("SWMCP_TEST_DIR") or os.environ.get("SWMCP_TESTS_DIR")
        if env_test_dir:
            roots.append(env_test_dir)
        roots.append(os.path.join(tempfile.gettempdir(), "SWMCP_Tests"))
        legacy_test_dir = r"C:\SWMCP_Tests"
        if os.path.exists(legacy_test_dir):
            roots.append(legacy_test_dir)

        # Resolve to canonical paths
        self.allowed_roots: List[str] = [
            os.path.realpath(r).lower() for r in roots if os.path.exists(r)
        ]

    def add_allowed_root(self, path: str) -> None:
        """Add an authorized directory root."""
        resolved = os.path.realpath(path).lower()
        if resolved not in self.allowed_roots and os.path.exists(resolved):
            self.allowed_roots.append(resolved)

    def is_path_allowed(self, path: str) -> bool:
        """Verify whether a path lies within authorized directory roots."""
        try:
            canonical = os.path.realpath(path).lower()
            return any(
                canonical == root or canonical.startswith(root + os.sep)
                for root in self.allowed_roots
            )
        except Exception:
            return False

    def validate_path(
        self,
        filepath: str,
        must_exist: bool = False,
        allowed_extensions: Optional[Set[str]] = None,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Validate path safety, extension, and existence.

        Returns:
            Tuple of (is_valid, canonical_path_or_none, error_message_or_none)
        """
        if not filepath or not filepath.strip():
            return False, None, "Filepath cannot be empty."

        clean_path = filepath.strip().strip('"').strip("'")

        # Canonicalize path
        try:
            canonical = os.path.realpath(clean_path)
        except Exception as e:
            return False, None, f"Invalid path syntax: {e}"

        # Verify allowed directory root
        if not self.is_path_allowed(canonical):
            return False, None, f"Access denied: path '{filepath}' is outside permitted directory boundaries."

        # Extension check
        ext = os.path.splitext(canonical)[1].lower()
        valid_exts = allowed_extensions or self.ALLOWED_CAD_EXTENSIONS
        if valid_exts and ext not in valid_exts:
            return (
                False,
                None,
                f"Unsupported file extension '{ext}'. Permitted extensions: {', '.join(sorted(valid_exts))}",
            )

        # Existence check
        if must_exist and not os.path.isfile(canonical):
            return False, None, f"File does not exist: '{canonical}'"

        return True, canonical, None


# Process-wide singleton
_GLOBAL_FS_POLICY: Optional[FileSystemPolicy] = None


def get_fs_policy() -> FileSystemPolicy:
    """Get or initialize the global FileSystemPolicy singleton."""
    global _GLOBAL_FS_POLICY
    if _GLOBAL_FS_POLICY is None:
        _GLOBAL_FS_POLICY = FileSystemPolicy()
    return _GLOBAL_FS_POLICY
