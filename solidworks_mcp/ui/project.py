"""
SolidWorks Project Board - MCP App state
----------------------------------------
Backs ui://solidworks/project.html for the solidworks-project workflow. It reads the
project folder's AGENT_CONTEXT.md (title, TRACKLIST table, open questions),
DECISIONS_LOG.md (CAD-nnn entries) and each module folder (CAD file counts, last
change, a SolidWorks preview thumbnail), and returns them as structuredContent.project:

    {
      "folder", "title", "context_file", "updated",
      "modules": [{"row", "number", "name", "folder", "status", "status_detail",
                   "evidence", "notes", "files": {"parts", "assemblies", "drawings"},
                   "main_file", "modified", "thumbnail"}],
      "counts": {"todo", "in progress", "blocked", "done"},
      "open_questions": [...], "decisions": {"count", "recent": [...]},
      "status"
    }

The board can change a module's Status cell in the tracklist (set_status) and open a
module's main file in SolidWorks (open). Everything else stays read-only.
"""

import base64
import datetime as _dt
import logging
import os
import re
import tempfile
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("SolidWorksMCP.Project")

RESOURCE_URI = "ui://solidworks/project.html"
STATUSES = ("todo", "in progress", "blocked", "done")
CONTEXT_NAMES = ("AGENT_CONTEXT.md",)
CONTEXT_PATTERNS = (re.compile(r".*agent[_ ]context.*\.md$", re.I), re.compile(r"^claude\.md$", re.I))
MAX_THUMBNAILS = 16
MAX_CONTEXT_BYTES = 2 * 1024 * 1024

_automation_instance: Any = None
_lock = threading.Lock()
_state: Dict[str, Any] = {}


def set_automation_instance(instance: Any) -> None:
    global _automation_instance
    _automation_instance = instance


def render_page() -> str:
    from .apps import render
    return render(RESOURCE_URI)


def get_state() -> Dict[str, Any]:
    return _state


# ============================================================================
# Paths
# ============================================================================

def _inside(root: str, path: str) -> bool:
    root_c = os.path.normcase(os.path.realpath(root))
    path_c = os.path.normcase(os.path.realpath(path))
    return path_c == root_c or path_c.startswith(root_c + os.sep)


def resolve_folder(folder: str) -> Tuple[Optional[str], Optional[str]]:
    """(canonical folder, error). The folder must exist and be inside the connector's allowed roots."""
    if not folder or not str(folder).strip():
        return None, "project_folder is required."
    path = os.path.realpath(str(folder).strip().strip('"'))
    if not os.path.isdir(path):
        return None, f"Project folder not found: {folder}"
    try:
        from ..core.fs_policy import get_fs_policy
        if not get_fs_policy().is_path_allowed(path):
            return None, f"Access denied: {folder} is outside the folders the connector may read."
    except Exception as e:  # the policy must never be skipped silently
        return None, f"Could not check folder permissions: {e}"
    return path, None


def find_context_file(folder: str) -> Optional[str]:
    """AGENT_CONTEXT.md, or a *Agent_Context*.md / CLAUDE.md, in the folder or its 99_Reference subfolder."""
    candidates = [folder] + [os.path.join(folder, d) for d in sorted(os.listdir(folder))
                             if d.lower().startswith("99_") and os.path.isdir(os.path.join(folder, d))]
    for directory in candidates:
        names = os.listdir(directory)
        for name in CONTEXT_NAMES:
            if name in names:
                return os.path.join(directory, name)
        for pattern in CONTEXT_PATTERNS:
            for name in sorted(names):
                if pattern.match(name) and os.path.isfile(os.path.join(directory, name)):
                    return os.path.join(directory, name)
    return None


# ============================================================================
# Markdown parsing
# ============================================================================

def _cells(line: str) -> List[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in body.split("|")]


def _is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*", line))


def _column_map(header: List[str]) -> Dict[str, int]:
    cols: Dict[str, int] = {}
    for i, h in enumerate(header):
        k = h.lower()
        if k in ("#", "no", "no.", "nr") and "number" not in cols:
            cols["number"] = i
        elif ("module" in k or "step" in k) and "name" not in cols:
            cols["name"] = i
        elif "folder" in k and "folder" not in cols:
            cols["folder"] = i
        elif "status" in k and "status" not in cols:
            cols["status"] = i
        elif "evidence" in k and "evidence" not in cols:
            cols["evidence"] = i
        elif ("note" in k or "comment" in k) and "notes" not in cols:
            cols["notes"] = i
    return cols


def find_tracklist(lines: List[str]) -> Optional[Tuple[int, Dict[str, int], List[int]]]:
    """(header line index, column map, data line indexes) of the first table with Module/Step and Status columns."""
    for i in range(len(lines) - 1):
        if "|" not in lines[i] or not _is_separator(lines[i + 1]):
            continue
        cols = _column_map(_cells(lines[i]))
        if "name" not in cols or "status" not in cols:
            continue
        rows = []
        j = i + 2
        while j < len(lines) and lines[j].strip().startswith("|"):
            rows.append(j)
            j += 1
        return i, cols, rows
    return None


def normalize_status(raw: str) -> Tuple[str, str]:
    """("todo" | "in progress" | "blocked" | "done", detail)."""
    text = re.sub(r"[*_`]", "", raw or "").strip()
    low = text.lower()
    detail = ""
    m = re.search(r"\((.*)\)", text)
    if m:
        detail = m.group(1).strip()
    if low.startswith("done") or low in ("complete", "completed", "finished") or "✅" in text:
        return "done", detail
    if low.startswith("blocked") or "waiting" in low:
        return "blocked", detail or (text if "waiting" in low else "")
    if low.startswith("in progress") or low.startswith("wip") or low.startswith("doing") or "progress" in low:
        return "in progress", detail
    return "todo", detail


def _section(lines: List[str], heading_pattern: str) -> List[str]:
    out: List[str] = []
    inside = False
    for line in lines:
        if re.match(r"^#{1,6}\s", line):
            if inside:
                break
            inside = bool(re.search(heading_pattern, line, re.I))
            continue
        if inside:
            out.append(line)
    return out


def open_questions(lines: List[str]) -> List[str]:
    items = []
    for line in _section(lines, r"open questions"):
        m = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)", line)
        if m:
            items.append(m.group(1))
        elif re.match(r"^\s*\|", line) and not _is_separator(line):
            cells = [c for c in _cells(line) if c]
            if cells and not re.fullmatch(r"(#|q|id|question)", cells[0], re.I):
                items.append(" - ".join(cells))
    return items[:50]


def decisions(folder: str) -> Dict[str, Any]:
    path = os.path.join(folder, "DECISIONS_LOG.md")
    if not os.path.isfile(path):
        return {"count": 0, "recent": []}
    entries = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.search(r"\bCAD-\d+\b.*", line)
            if m:
                entries.append(re.sub(r"^[#\s|*-]+", "", line.strip())[:240])
    return {"count": len(entries), "recent": entries[-5:][::-1]}


# ============================================================================
# Module folders
# ============================================================================

CAD_EXT = {".sldprt": "parts", ".sldasm": "assemblies", ".slddrw": "drawings"}


def _folder_info(project: str, folder: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {"files": {"parts": 0, "assemblies": 0, "drawings": 0}, "main_file": None, "modified": None}
    if not folder:
        return info
    path = os.path.join(project, folder.strip().strip("/\\"))
    if not os.path.isdir(path) or not _inside(project, path):
        return info
    newest = 0.0
    best: Tuple[int, float, str] = (-1, 0.0, "")
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "~"))]
        for name in filenames:
            if name.startswith("~$"):
                continue  # SolidWorks lock files
            ext = os.path.splitext(name)[1].lower()
            if ext not in CAD_EXT:
                continue
            full = os.path.join(dirpath, name)
            info["files"][CAD_EXT[ext]] += 1
            mtime = os.path.getmtime(full)
            newest = max(newest, mtime)
            rank = 2 if ext == ".sldasm" else 1 if ext == ".sldprt" else 0
            if (rank, mtime) > best[:2]:
                best = (rank, mtime, full)
    if best[2] and best[0] > 0:
        info["main_file"] = best[2]
    if newest:
        info["modified"] = _dt.datetime.fromtimestamp(newest).isoformat(timespec="minutes")
    return info


def thumbnail(path: str) -> str:
    """The preview bitmap SolidWorks stores in the file, as a data URI ('' when unavailable)."""
    app = getattr(_automation_instance, "_sw_app", None) if _automation_instance is not None else None
    if app is None or not path:
        return ""
    fd, bmp = tempfile.mkstemp(prefix="swmcp_thumb_", suffix=".bmp")
    os.close(fd)
    try:
        ok = app.GetPreviewBitmapFile(path, "", bmp)
        if ok and os.path.getsize(bmp) > 0:
            with open(bmp, "rb") as f:
                return "data:image/bmp;base64," + base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        logger.debug(f"project board: no preview for {path}: {e}")
    finally:
        try:
            os.remove(bmp)
        except OSError:
            pass
    return ""


# ============================================================================
# Load / actions
# ============================================================================

def load(folder: str, with_thumbnails: bool = True) -> Dict[str, Any]:
    """Read the project. Returns {success, message, data}."""
    project, err = resolve_folder(folder)
    if err:
        return {"success": False, "message": err, "data": None}
    context = find_context_file(project)
    if not context:
        return {"success": False, "data": None, "message": (
            f"No AGENT_CONTEXT.md in {project}. Set the project up first (solidworks-project skill, Steps 2-5).")}
    if os.path.getsize(context) > MAX_CONTEXT_BYTES:
        return {"success": False, "message": f"{context} is too large to read.", "data": None}
    with open(context, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    title = os.path.basename(project)
    for line in lines:
        m = re.match(r"^#\s+(.*\S)", line)
        if m:
            title = re.split(r"\s+[—–-]\s+project context", m.group(1), flags=re.I)[0].strip()
            break

    modules: List[Dict[str, Any]] = []
    table = find_tracklist(lines)
    thumbs = 0
    if table:
        _hdr, cols, rows = table
        for row_index in rows:
            cells = _cells(lines[row_index])
            get = lambda k: cells[cols[k]] if k in cols and cols[k] < len(cells) else ""  # noqa: E731
            status, detail = normalize_status(get("status"))
            module_folder = get("folder").strip("` ")
            info = _folder_info(project, module_folder)
            thumb = ""
            if with_thumbnails and info["main_file"] and thumbs < MAX_THUMBNAILS:
                thumb = thumbnail(info["main_file"])
                thumbs += 1 if thumb else 0
            modules.append({
                "row": row_index,
                "number": get("number"),
                "name": get("name"),
                "folder": module_folder,
                "status": status,
                "status_detail": detail,
                "evidence": get("evidence"),
                "notes": get("notes"),
                "files": info["files"],
                "main_file": info["main_file"],
                "modified": info["modified"],
                "thumbnail": thumb,
            })

    counts = {s: sum(1 for m in modules if m["status"] == s) for s in STATUSES}
    state = {
        "folder": project,
        "title": title,
        "context_file": context,
        "updated": _dt.datetime.fromtimestamp(os.path.getmtime(context)).isoformat(timespec="minutes"),
        "modules": modules,
        "counts": counts,
        "open_questions": open_questions(lines),
        "decisions": decisions(project),
        "status": ("No TRACKLIST table (columns Module/Step and Status) was found in the context file."
                   if not table else f"{counts['done']} of {len(modules)} modules done."),
    }
    _state.clear()
    _state.update(state)
    return {"success": True, "message": "", "data": state}


def set_status(row: int, status: str, detail: str = "") -> Tuple[bool, str]:
    """Rewrite the Status cell of one tracklist row in the context file. Returns (ok, message)."""
    context = _state.get("context_file")
    if not context or not os.path.isfile(context):
        return False, "Open the board first."
    if status not in STATUSES:
        return False, f"status must be one of {', '.join(STATUSES)}"
    with open(context, encoding="utf-8", errors="replace", newline="") as f:
        raw = f.read()
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(newline)
    table = find_tracklist(lines)
    if not table or row not in table[2]:
        return False, "That row is no longer in the tracklist. Refresh the board."
    expected = next((m for m in _state.get("modules", []) if m["row"] == row), None)
    cells = _cells(lines[row])
    cols = table[1]
    if expected and cols.get("name") is not None and cols["name"] < len(cells) and cells[cols["name"]] != expected["name"]:
        return False, "The tracklist changed since the board was loaded. Refresh the board."
    clean_detail = re.sub(r"[|\r\n()]", " ", detail or "").strip()
    cells[cols["status"]] = f"{status} ({clean_detail})" if status == "blocked" and clean_detail else status
    lines[row] = "| " + " | ".join(cells) + " |"
    with open(context, "w", encoding="utf-8", newline="") as f:
        f.write(newline.join(lines))
    return True, f"Marked '{expected['name'] if expected else row}' as {status}."


def handle_action(action: str, row: Any = None, status: Any = None, detail: Any = None) -> Dict[str, Any]:
    """Run one board action (refresh, set_status, open) and return {success, message, data}."""
    with _lock:
        folder = _state.get("folder")
        if not folder:
            return {"success": False, "message": "Open the board with show_project_board first.", "data": None}
        message = ""
        if action == "set_status":
            try:
                ok, message = set_status(int(row), str(status or ""), str(detail or ""))
            except (TypeError, ValueError):
                ok, message = False, "row must be a tracklist row number."
            if not ok:
                return {"success": False, "message": message, "data": None}
        elif action == "open":
            module = next((m for m in _state.get("modules", []) if str(m["row"]) == str(row)), None)
            if not module or not module.get("main_file"):
                return {"success": False, "message": "That module has no part or assembly file yet.", "data": None}
            if not _inside(folder, module["main_file"]):
                return {"success": False, "message": "Refusing to open a file outside the project folder.", "data": None}
            res = _automation_instance.open_document(filepath=module["main_file"])
            if not (isinstance(res, dict) and res.get("success")):
                return {"success": False, "data": None,
                        "message": (res or {}).get("message", "Could not open the file.") if isinstance(res, dict) else "Could not open the file."}
            message = f"Opened {os.path.basename(module['main_file'])} in SolidWorks."
        elif action != "refresh":
            return {"success": False, "message": f"Unknown project action '{action}'.", "data": None}
        res = load(folder, with_thumbnails=True)
        if res["success"] and message:
            res["data"]["status"] = message
        return res
