"""Print this interpreter's path if it can run the SolidCADWorks server (Python 3.10+), else nothing."""
import sys

if sys.version_info >= (3, 10):
    print(sys.executable)
