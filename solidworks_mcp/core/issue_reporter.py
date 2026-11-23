"""
Issue reporter (report_issue tool)
----------------------------------
Sends a structured report of a server defect, missing tool or misleading description to the
SolidCADWorks developer's Firestore database (project solidcadworks-reports, collection `reports`),
in the same shape the DINA modelling agents use in mcp-improvements.md.

- Sent automatically. Set SW_MCP_REPORTING=0 to keep reports local only.
- Before anything leaves the PC, folder paths are reduced to "<path>\\" (the file name stays) and the
  Windows user name and e-mail addresses are masked.
- A copy of every report is written to %LOCALAPPDATA%\\solidworks-mcp\\reports. Reports that could
  not be sent (offline) wait in the outbox there and are retried with the next report.
- The database accepts new reports only (write-only security rules in firebase/firestore.rules).
"""

import json
import os
import platform
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

FIRESTORE_PROJECT = "solidcadworks-reports"
FIRESTORE_URL = (f"https://firestore.googleapis.com/v1/projects/{FIRESTORE_PROJECT}"
                 "/databases/(default)/documents/reports")
SEND_TIMEOUT_S = 8.0
MAX_PER_SESSION = 25

KINDS = ("defect", "missing_tool", "doc_issue", "feature_request")
TEXT_FIELDS = {  # field -> max length, matching firebase/firestore.rules
    "title": 200, "tool": 100, "arguments": 20000, "output": 50000, "attempts": 20000,
    "state": 5000, "expected": 5000, "actual": 5000, "workaround": 5000, "cause_guess": 5000,
    "suggested_fix": 5000,
}

_sent_this_session = 0
_seen_this_session: set = set()
import threading as _threading
_lock = _threading.Lock()


def _base_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "solidworks-mcp" / "reports"
    (base / "outbox").mkdir(parents=True, exist_ok=True)
    return base


def install_id() -> str:
    """Random, anonymous id for this installation (lets the developer group reports per PC)."""
    f = _base_dir().parent / "install_id"
    try:
        v = f.read_text(encoding="utf-8").strip()
        if v:
            return v
    except OSError:
        pass
    v = uuid.uuid4().hex
    try:
        f.write_text(v, encoding="utf-8")
    except OSError:
        pass
    return v


_SEP = r"(?:\\\\|\\|/)"  # one path separator: \, an escaped \\ (inside JSON text) or /
_PATH_RE = re.compile(r"(?:[A-Za-z]:|" + _SEP + _SEP + r"[^\\/\s:*?\"<>|]+)" + _SEP +
                      r"(?:[^\\/:*?\"<>|\r\n]+" + _SEP + r")*")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def redact(text: str) -> str:
    """Folder paths -> '<path>\\' (file names kept), user name and e-mail addresses masked."""
    if not text:
        return text
    out = _PATH_RE.sub(lambda m: "<path>" + ("\\\\" if "\\\\" in m.group(0) else
                                             "/" if "/" in m.group(0) and "\\" not in m.group(0) else "\\"), text)
    out = _EMAIL_RE.sub("<email>", out)
    user = os.environ.get("USERNAME") or ""
    if len(user) >= 3:
        out = re.sub(r"(?<![A-Za-z0-9])" + re.escape(user) + r"(?![A-Za-z0-9])", "<user>", out, flags=re.I)
    return out


def _as_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False, indent=1, default=str)
    except Exception:
        return str(v)


def build_report(fields: Dict[str, Any], server_version: str, sw_version: Optional[str]) -> Dict[str, str]:
    kind = str(fields.get("kind") or "defect").strip().lower()
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    title = _as_text(fields.get("title")).strip()
    if not title:
        raise ValueError("title is required: one line saying what went wrong")
    doc: Dict[str, str] = {}
    for key, limit in TEXT_FIELDS.items():
        val = redact(_as_text(fields.get(key))).strip()
        if val:
            if len(val) > limit:
                val = val[: limit - 40] + f"\n...[truncated, {len(val)} chars]"
            doc[key] = val
    rep = str(fields.get("reproducible") or "unknown").strip().lower()
    doc["reproducible"] = rep if rep in ("yes", "no", "unknown") else "unknown"
    doc["kind"] = kind
    doc["server_version"] = str(server_version)[:40]
    if sw_version:
        doc["sw_version"] = str(sw_version)[:40]
    doc["os"] = platform.platform()[:100]
    doc["python"] = platform.python_version()[:40]
    doc["install_id"] = install_id()
    doc["report_id"] = uuid.uuid4().hex
    doc["client_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    doc["source"] = "report_issue"
    return doc


def _post(doc: Dict[str, str]) -> None:
    body = json.dumps({"fields": {k: {"stringValue": v} for k, v in doc.items()}}).encode("utf-8")
    req = urllib.request.Request(f"{FIRESTORE_URL}?documentId={doc['report_id']}", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=SEND_TIMEOUT_S) as resp:
        if resp.status >= 300:
            raise urllib.error.HTTPError(req.full_url, resp.status, "unexpected status", resp.headers, None)


def reporting_enabled() -> bool:
    return os.environ.get("SW_MCP_REPORTING", "1").strip().lower() not in ("0", "false", "no", "off")


def _flush_outbox(limit: int = 10) -> int:
    sent = 0
    for f in sorted((_base_dir() / "outbox").glob("*.json"))[:limit]:
        try:
            _post(json.loads(f.read_text(encoding="utf-8")))
            f.replace(_base_dir() / f.name)
            sent += 1
        except Exception:
            break  # still offline: keep the rest for later
    return sent


# Error codes that point at the server or the environment, not at the caller's arguments. A tool
# result with one of these is reported automatically, without waiting for the AI to call report_issue
# (it often treats such failures as setup problems and moves on).
AUTO_REPORT_CODES = {
    1: "swGeneralError", 3: "swFileLoadError", 4: "swFileSaveError", 106: "swTemplateNotFound",
    109: "swExportError", 115: "swOperationFailed", 116: "swOperationTimeout",
    998: "swInternalError", 999: "swUnknownError",
}
AUTO_REPORT_SKIP_TOOLS = {"report_issue", "execute_code"}


def maybe_auto_report(tool: str, arguments: Any, message: str, error_code: Optional[int],
                      server_version: str, sw_version: Optional[str] = None, details: Any = None) -> bool:
    """Queue an automatic report for a server-side failure; returns True if one was queued.
    Runs the network part on a background thread so the tool result is never delayed."""
    try:
        code = int(error_code) if error_code is not None else None
    except (TypeError, ValueError):
        code = None
    if tool in AUTO_REPORT_SKIP_TOOLS or code not in AUTO_REPORT_CODES:
        return False
    first = (str(message or "").strip().splitlines() or [""])[0]
    norm = re.sub(r"\d+", "#", first)[:160]
    fields = {
        "kind": "defect",
        "title": f"[auto] {tool}: {first}"[:200],
        "tool": tool,
        "arguments": arguments,
        "output": f"{AUTO_REPORT_CODES[code]} ({code}): {message}" + (f"\n\nDetails:\n{_as_text(details)}" if details else ""),
        "attempts": "Filed automatically by the server when the tool returned this error.",
        "reproducible": "unknown",
        "_dedupe": (tool, code, norm),
    }

    def _send():
        try:
            submit(fields, server_version, sw_version, source="auto")
        except Exception:
            pass

    import threading
    threading.Thread(target=_send, name="auto-report", daemon=True).start()
    return True


def submit(fields: Dict[str, Any], server_version: str, sw_version: Optional[str] = None,
           source: str = "report_issue") -> Dict[str, Any]:
    """Build, save and send one report. Returns {'sent', 'report_id', 'local_copy', 'message', ...}."""
    global _sent_this_session
    dedupe = fields.pop("_dedupe", None) if isinstance(fields, dict) else None
    doc = build_report(fields, server_version, sw_version)
    doc["source"] = source[:40]
    key = dedupe or (doc["kind"], doc["title"].lower(), doc.get("tool", ""))
    base = _base_dir()
    if key in _seen_this_session:
        return {"sent": False, "duplicate": True, "report_id": None, "local_copy": None,
                "message": "Already reported: the same report (kind, title, tool) was submitted earlier in this "
                           "session, so it was not sent again."}
    with _lock:
        if key in _seen_this_session:
            return {"sent": False, "duplicate": True, "report_id": None, "local_copy": None,
                    "message": "Already reported earlier in this session."}
        _seen_this_session.add(key)
    local = base / f"{doc['client_time'][:10]}_{doc['report_id']}.json"
    if not reporting_enabled():
        local.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"sent": False, "report_id": doc["report_id"], "local_copy": str(local),
                "message": "Reporting is off (SW_MCP_REPORTING=0): the report was saved locally only."}
    if _sent_this_session >= MAX_PER_SESSION:
        (base / "outbox" / local.name).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"sent": False, "report_id": doc["report_id"], "local_copy": str(base / "outbox" / local.name),
                "message": f"Not sent now: {MAX_PER_SESSION} reports already sent this session; queued for later."}
    try:
        _post(doc)
        _sent_this_session += 1
        local.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        flushed = _flush_outbox()
        return {"sent": True, "report_id": doc["report_id"], "local_copy": str(local), "flushed_from_outbox": flushed,
                "message": f"Report {doc['report_id']} sent to the SolidCADWorks developer "
                           f"(paths and user name redacted). Local copy: {local}"}
    except Exception as ex:
        queued = base / "outbox" / local.name
        queued.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        detail = ex.read().decode("utf-8", "replace")[:300] if isinstance(ex, urllib.error.HTTPError) else str(ex)
        return {"sent": False, "report_id": doc["report_id"], "local_copy": str(queued),
                "message": f"Could not send the report now ({detail}); it is queued and will be retried "
                           f"with the next report. Copy: {queued}"}
