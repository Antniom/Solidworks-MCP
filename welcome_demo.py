"""
SolidWorks MCP: 3D Welcome Demonstration
----------------------------------------
Root launcher script to create the 3D "Welcome to Solidworks AI" extruded plaque.

Usage:
    python welcome_demo.py
    python -m solidworks_mcp.welcome_demo
"""

import sys
from pathlib import Path

# Ensure package is importable when executed directly from repository root
repo_root = Path(__file__).resolve().parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from solidworks_mcp.welcome_demo import main

if __name__ == "__main__":
    main()
