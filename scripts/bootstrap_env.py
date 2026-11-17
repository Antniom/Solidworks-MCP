"""
SolidCADWorks environment bootstrap (used by start-server.cmd when uv is not installed).

    bootstrap_env.py ensure  <plugin_root> <data_dir>
    bootstrap_env.py install <plugin_root> <data_dir>

`ensure` returns 0 at once when <data_dir>/venv already holds the dependencies listed in
pyproject.toml. Otherwise it starts `install` as a separate background process and waits for it
for up to WAIT_S seconds. The install keeps running if the MCP client gives up on the first
connection (clients wait about 30 s), so the next connection finds it finished. Everything is
written to stderr or to <data_dir>/install.log: stdout belongs to the MCP protocol.

Standard library only; works on Python 3.10+.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BOOTSTRAP_VERSION = "1"
WAIT_S = 25.0
STALE_LOCK_S = 30 * 60


def log(msg: str) -> None:
    sys.stderr.write(f"SolidCADWorks: {msg}\n")
    sys.stderr.flush()


def read_dependencies(root: Path) -> list:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib  # Python 3.11+
        return list(tomllib.loads(text)["project"]["dependencies"])
    except ImportError:
        pass
    project = text.split("[project]", 1)[1]
    block = re.search(r"^dependencies\s*=\s*\[(.*?)\]", project, re.S | re.M)
    return re.findall(r'"([^"]+)"', block.group(1)) if block else []


def paths(data: Path):
    venv = data / "venv"
    return venv, venv / "Scripts" / "python.exe", data / "env.stamp", data / "install.lock", data / "install.log"


def wanted_stamp(root: Path) -> str:
    payload = {"bootstrap": BOOTSTRAP_VERSION, "python": list(sys.version_info[:2]),
               "deps": sorted(read_dependencies(root))}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def is_ready(root: Path, data: Path) -> bool:
    _venv, py, stamp, _lock, _log = paths(data)
    try:
        return py.exists() and stamp.read_text(encoding="utf-8").strip() == wanted_stamp(root)
    except OSError:
        return False


def pid_alive(pid: int) -> bool:
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    except Exception:
        return True


def lock_is_live(lock: Path) -> bool:
    if not lock.exists():
        return False
    try:
        pid = int((lock / "pid").read_text().strip())
    except (OSError, ValueError):
        pid = 0
    age = time.time() - lock.stat().st_mtime
    if age > STALE_LOCK_S or (pid and not pid_alive(pid)):
        try:
            for f in lock.iterdir():
                f.unlink()
            lock.rmdir()
        except OSError:
            pass
        return False
    return True


def start_installer(root: Path, data: Path) -> None:
    _venv, _py, _stamp, _lock, logf = paths(data)
    data.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        # Hidden console (pip/venv children inherit it, so no windows pop up), own process group,
        # and out of the client's job object so closing the MCP connection does not kill it.
        flags = 0x08000000 | 0x00000200 | 0x01000000  # NO_WINDOW | NEW_PROCESS_GROUP | BREAKAWAY_FROM_JOB
    args = [sys.executable, str(Path(__file__).resolve()), "install", str(root), str(data)]
    out = open(logf, "a", encoding="utf-8")
    try:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=out, creationflags=flags, close_fds=True)
    except OSError:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         creationflags=flags & ~0x01000000, close_fds=True)


def ensure(root: Path, data: Path) -> int:
    if is_ready(root, data):
        return 0
    _venv, _py, _stamp, lock, logf = paths(data)
    if not lock_is_live(lock):
        log(f"first start: installing the Python dependencies into {data} (about a minute) ...")
        start_installer(root, data)
    deadline = time.time() + WAIT_S
    while time.time() < deadline:
        if is_ready(root, data):
            log("dependencies ready.")
            return 0
        time.sleep(1.0)
    log("still installing the Python dependencies. Wait a minute, then reconnect the server "
        f"(/mcp in Claude Code). Progress: {logf}")
    return 75


def install(root: Path, data: Path) -> int:
    venv, py, stamp, lock, _log = paths(data)
    data.mkdir(parents=True, exist_ok=True)
    try:
        lock.mkdir()
    except FileExistsError:
        if lock_is_live(lock):
            log("another install is running; leaving it to finish.")
            return 0
        lock.mkdir()
    (lock / "pid").write_text(str(os.getpid()))
    try:
        log(f"{time.ctime()}: preparing {venv} with {sys.executable} ({sys.version.split()[0]})")
        if not py.exists():
            subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
        deps = read_dependencies(root)
        log("installing: " + ", ".join(deps))
        subprocess.check_call([str(py), "-m", "pip", "install", "--disable-pip-version-check",
                               "--no-input", "--upgrade", *deps])
        stamp.write_text(wanted_stamp(root), encoding="utf-8")
        log("done.")
        return 0
    except subprocess.CalledProcessError as e:
        log(f"install failed ({e}). See the lines above; fix the cause and reconnect to retry.")
        return 1
    finally:
        try:
            (lock / "pid").unlink()
            lock.rmdir()
        except OSError:
            pass


def main(argv: list) -> int:
    if len(argv) != 4 or argv[1] not in ("ensure", "install"):
        log("usage: bootstrap_env.py ensure|install <plugin_root> <data_dir>")
        return 2
    root, data = Path(argv[2]).resolve(), Path(argv[3])
    return ensure(root, data) if argv[1] == "ensure" else install(root, data)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
