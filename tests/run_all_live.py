"""
Master test runner for all live SolidWorks MCP bug tests (B1 - B15).
Runs each test in an isolated process to prevent COM apartment leakage,
ensures clean setup and teardown between tests, and prints a formatted
pass/fail results table.
"""
import sys
import os
import time
import tempfile
from pathlib import Path

default_tests_dir = r"C:\SWMCP_Tests\fix-20261007" if os.path.exists(r"C:\SWMCP_Tests\fix-20261007") else os.path.join(tempfile.gettempdir(), "SWMCP_Tests", "fix-20261007")
TESTS_DIR = Path(os.environ.get("SWMCP_TEST_DIR", default_tests_dir))
PYTHON_EXE = Path(sys.executable)

TEST_MANIFEST = [
    ("test_b1_live.py", "B1: Rebuild Errors", "Feature error traversal and GetErrorCode2 detection"),
    ("test_b2_live.py", "B2: Fillet Edges", "Fillet error handling and selection validation"),
    ("test_b3_live.py", "B3: Modal Watchdog", "Non-blocking dialog watchdog and dismiss_dialog"),
    ("test_b4_live.py", "B4: Extrude & Cut", "FX-BLOCK 100x50x20 extrusion & 10mm cut verification"),
    ("test_b5_live.py", "B5: Save Document", "Save without path and ByRef Save3 handling"),
    ("test_b6_live.py", "B6: STEP Import", "Silent LoadFile4 STEP export & import volume match"),
    ("test_b7_live.py", "B7: Toolbox Add-in", "Toolbox DLL path and programmatic load verification"),
    ("test_b8.py",      "B8: Filesystem Policy", "Parasolid .x_t and .x_b extension allowlist"),
    ("test_b9_live.py", "B9: Simulation FEA", "FX-BEAM static FEA study, tip deflection and stress"),
    ("test_b10_live.py", "B10: Assemblies & Mates", "Mate error envelopes and conflict diagnostics"),
    ("test_b11_live.py", "B11: Input Validation", "Strict schema bounds and error rejections"),
    ("test_b12_live.py", "B12: Close Document", "Close document rejection when no document is open"),
    ("test_b13_live.py", "B13: Connection Robust", "PID targeting, RPC auto-reconnect, and is_connected"),
    ("test_b15_live.py", "B15: Python Security", "Guarded execute_python config security flag"),
]


def cleanup_lingering_documents():
    """Ensure all open documents are closed before running a test."""
    try:
        import win32com.client
        sw = win32com.client.GetActiveObject("SldWorks.Application")
        for _ in range(20):
            cnt = getattr(sw, "GetDocumentCount", 0)
            if callable(cnt):
                cnt = cnt()
            if not cnt or cnt <= 0:
                break
            doc = getattr(sw, "ActiveDoc", None)
            if not doc:
                break
            title = getattr(doc, "GetTitle", None)
            if callable(title):
                title = title()
            if not title:
                break
            sw.CloseDoc(str(title))
    except Exception:
        pass


def run_suite():
    print("=" * 80)
    print("      SOLIDWORKS MCP LIVE TEST SUITE (B1 - B15)")
    print("=" * 80)
    print(f"Target Python: {PYTHON_EXE}")
    print(f"Test Directory: {TESTS_DIR}\n")

    results = []
    total_start = time.time()

    for script_name, test_label, test_desc in TEST_MANIFEST:
        script_path = TESTS_DIR / script_name
        if not script_path.exists():
            results.append((test_label, test_desc, "MISSING", 0.0, f"File not found: {script_name}"))
            continue

        cleanup_lingering_documents()
        print(f"Running [{test_label}] ({script_name})...", end="", flush=True)
        t0 = time.time()

        proc = subprocess.run(
            [str(PYTHON_EXE), str(script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180
        )
        elapsed = time.time() - t0

        if proc.returncode == 0:
            print(f" PASS ({elapsed:.1f}s)")
            results.append((test_label, test_desc, "PASS", elapsed, "Clean execution"))
        else:
            print(f" FAIL ({elapsed:.1f}s)")
            err_snippet = (proc.stderr or proc.stdout)[-300:].strip().replace("\n", " ")
            results.append((test_label, test_desc, "FAIL", elapsed, err_snippet))

    cleanup_lingering_documents()
    total_elapsed = time.time() - total_start

    print("\n" + "=" * 80)
    print("                        TEST RESULTS SUMMARY")
    print("=" * 80)
    header = f"| {'Test ID':<22} | {'Description':<35} | {'Status':<6} | {'Time (s)':<8} |"
    divider = f"|{'-'*24}|{'-'*37}|{'-'*8}|{'-'*10}|"
    print(header)
    print(divider)

    all_passed = True
    for label, desc, status, t_sec, details in results:
        if status != "PASS":
            all_passed = False
        print(f"| {label:<22} | {desc:<35} | {status:<6} | {t_sec:>8.2f} |")

    print(divider)
    passed_count = sum(1 for r in results if r[2] == "PASS")
    total_count = len(results)
    print(f"\nTotal: {passed_count}/{total_count} Passed ({total_elapsed:.1f}s total execution time)")

    if not all_passed:
        print("\nFailures Detail:")
        for label, desc, status, t_sec, details in results:
            if status != "PASS":
                print(f"  - [{label}]: {details}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(run_suite())
