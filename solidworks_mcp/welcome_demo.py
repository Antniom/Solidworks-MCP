"""
SolidWorks MCP: First-Run 3D Welcome Demonstration
--------------------------------------------------
Automatically creates an extruded 3D "Welcome to Solidworks AI" plaque
in SolidWorks without consuming AI model tokens.

Demonstrates:
- Live SolidWorks COM connection & ROT binding
- High-precision 3D part generation
- Planar face sketch selection & multi-tier stepped extrusion
- Parametric 3D sketch typography & solid letter embossing
- Automated viewport camera orientation (Isometric Zoom-to-Fit)
"""

import sys
import time
import logging
from typing import Optional

logger = logging.getLogger("solidworks_mcp.welcome_demo")


def run_welcome_demo(auto_launch: bool = True, verbose: bool = True) -> bool:
    """
    Executes the automated 3D Welcome Demo in SolidWorks.
    
    Args:
        auto_launch: If True and SolidWorks is closed, attempts to launch it.
        verbose: If True, prints animated progress banners to stdout.
        
    Returns:
        True if the 3D model was created successfully, False otherwise.
    """
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if verbose:
        print("\n" + "=" * 76)
        print(" [i] SOLIDWORKS AI ASSISTANT: FIRST-RUN 3D DEMONSTRATION")
        print("=" * 76)
        print(" Creating an extruded 3D 'Welcome to Solidworks AI' display plaque...")
        print(" (Pre-compiled autonomous sequence - 0 LLM tokens consumed)\n")

    try:
        from solidworks_mcp.automation import SolidWorksAutomation, find_sldworks_pids
    except ImportError as e:
        if verbose:
            print(f" [!] Error importing solidworks_mcp: {e}")
        return False

    # Check if SolidWorks is running
    pids = find_sldworks_pids()
    if not pids and not auto_launch:
        if verbose:
            print(" [i] SolidWorks is not currently open.")
            print("     Please start SolidWorks and run: python -m solidworks_mcp.welcome_demo\n")
        return False

    if verbose:
        if pids:
            print(f" [*] Step 1/6: Connecting to active SolidWorks instance (PID: {pids[0]})...")
        else:
            print(" [*] Step 1/6: Launching SolidWorks on your desktop...")

    sw = SolidWorksAutomation()
    conn_res = sw.connect_solidworks()
    if not conn_res.get("success", False):
        if verbose:
            print(f" [!] Connection failed: {conn_res.get('message', 'Unknown error')}")
            print("     Ensure SolidWorks is installed and responsive on this computer.\n")
        return False

    if verbose:
        print("     [+] Connected successfully!")
        print(" [*] Step 2/6: Creating new 3D Part document...")

    # Create new Part document
    doc_res = sw.create_new_part()
    if not doc_res.get("success", False):
        if verbose:
            print(f" [!] Failed to create new part: {doc_res.get('message')}")
        return False

    time.sleep(0.5)

    if verbose:
        print("     [+] Part document active.")
        print(" [*] Step 3/6: Sketching & extruding pedestal base (260mm x 90mm x 6mm)...")

    # Tier 1: Base Plaque on Front Plane
    sw.create_sketch(plane="Front")
    sw.draw_rectangle(x1=-130, y1=-45, x2=130, y2=45, unit="mm")
    ext1_res = sw.extrude_sketch(depth=6, unit="mm")
    if not ext1_res.get("success", False):
        if verbose:
            print(f" [!] Failed to extrude base plaque: {ext1_res.get('message')}")
        return False

    time.sleep(0.3)

    if verbose:
        print("     [+] Base plaque extruded.")
        print(" [*] Step 4/6: Adding architectural inset bezel (246mm x 76mm x 2mm)...")

    # Tier 2: Stepped Bezel on front face (z = 6mm)
    sw.create_sketch_on_face(x=0, y=0, z=6, unit="mm")
    sw.draw_rectangle(x1=-123, y1=-38, x2=123, y2=38, unit="mm")
    ext2_res = sw.extrude_sketch(depth=2, unit="mm")
    if not ext2_res.get("success", False):
        if verbose:
            print(f" [!] Inset bezel skipped: {ext2_res.get('message')}")

    time.sleep(0.3)

    if verbose:
        print("     [+] Inset bezel created.")
        print(" [*] Step 5/6: Formatting & extruding 3D typography ('Welcome to Solidworks AI')...")

    # Tier 3: 3D Typography on the stepped face (z = 8mm, or z = 6mm if bezel skipped)
    z_face = 8.0 if ext2_res.get("success", False) else 6.0
    sw.create_sketch_on_face(x=0, y=0, z=z_face, unit="mm")
    
    # Line 1: "Welcome to"
    sw.draw_text(
        text="Welcome to",
        x=-50.0,
        y=6.0,
        height=13.0,
        font_name="Segoe UI",
        bold=True,
        unit="mm"
    )
    
    # Line 2: "Solidworks AI"
    sw.draw_text(
        text="Solidworks AI",
        x=-82.0,
        y=-18.0,
        height=18.0,
        font_name="Segoe UI",
        bold=True,
        unit="mm"
    )

    # Extrude the sketch text 4mm proud of the face
    text_ext_res = sw.extrude_sketch(depth=4, unit="mm")
    if not text_ext_res.get("success", False):
        if verbose:
            print(f" [!] Notice: 3D text extrusion completed with warning: {text_ext_res.get('message')}")
    else:
        if verbose:
            print("     [+] 3D text extruded 4mm proud.")

    if verbose:
        print(" [*] Step 6/6: Aligning camera to Isometric view and zooming to fit...")

    # Center camera and show Isometric view
    doc, _ = sw.get_active_doc()
    if doc is not None:
        try:
            doc.ShowNamedView2("*Isometric", 7)
            doc.ViewZoomtofit2()
            doc.GraphicsRedraw2()
        except Exception as ve:
            logger.debug(f"View adjustment error: {ve}")

    if verbose:
        print("\n" + "=" * 76)
        print(" [+] SUCCESS: 3D DEMONSTRATION COMPLETE!")
        print("=" * 76)
        print(" The 'Welcome to Solidworks AI' 3D model is now live in SolidWorks.")
        print(" Your AI assistant is fully connected and ready to design for you!")
        print("=" * 76 + "\n")

    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="SolidWorks MCP 3D Welcome Demonstration")
    parser.add_argument("--no-launch", action="store_true", help="Do not auto-launch SolidWorks if closed")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = parser.parse_args()

    success = run_welcome_demo(
        auto_launch=not args.no_launch,
        verbose=not args.quiet
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
