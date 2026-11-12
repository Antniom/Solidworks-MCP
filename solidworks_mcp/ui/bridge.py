"""
SolidWorks MCP - Studio Bridge
------------------------------
Local HTTP server (127.0.0.1 only) that backs the interactive studio.

It serves the A2UI host page, hands out the A2UI messages that describe the
studio surface, receives A2UI client actions (slider moves, button presses) and
pushes A2UI data-model updates to every connected page over Server-Sent Events.

Security: every /api and /studio request must carry the per-process token that
is embedded in the generated page, and the Host header must be a loopback name.
Without both, a web page open in the user's browser could drive SolidWorks.
"""

import base64
import json
import logging
import os
import queue
import secrets
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

from . import a2ui

logger = logging.getLogger("SolidWorksMCP.StudioBridge")

UI_DIR = Path(__file__).parent
SHELL_PATH = UI_DIR / "shell.html"
BUNDLE_PATH = UI_DIR / "static" / "studio.bundle.js"

DEFAULT_PORT_START = 54321
DEFAULT_PORT_MAX = 54330
MAX_BODY_BYTES = 64 * 1024

_TOKEN = secrets.token_urlsafe(24)
_server_instance: Optional["StudioBridgeServer"] = None
_server_thread: Optional[threading.Thread] = None
_bridge_port: Optional[int] = None
_automation_instance: Any = None
_action_lock = threading.Lock()
_sse_clients: List["queue.Queue"] = []
_sse_lock = threading.Lock()


class StudioSession:
    """State of the studio surface currently on display."""

    def __init__(self) -> None:
        self.title = "SolidWorks Model"
        self.dims: Dict[str, Dict[str, Any]] = {}
        self.image = ""
        self.bbox: Optional[Dict[str, Any]] = None
        self.view = "*Isometric"
        self.status = "Open a model and call show_interactive_studio."

    def messages(self) -> List[a2ui.Message]:
        return a2ui.build_surface_messages(
            model_title=self.title,
            dims=self.dims,
            image_data_uri=self.image,
            bbox=self.bbox,
            view_name=self.view,
            status=self.status,
        )


_session = StudioSession()


# ============================================================================
# Public helpers used by the MCP server
# ============================================================================

def get_token() -> str:
    """Secret that pages must present to talk to the bridge."""
    return _TOKEN


def get_bridge_port() -> Optional[int]:
    return _bridge_port


def get_bridge_url() -> Optional[str]:
    return f"http://127.0.0.1:{_bridge_port}" if _bridge_port else None


def get_studio_url() -> Optional[str]:
    base = get_bridge_url()
    return f"{base}/studio?token={_TOKEN}" if base else None


def get_session() -> StudioSession:
    return _session


def set_automation_instance(instance: Any) -> None:
    """Inject the automation object the bridge should drive."""
    global _automation_instance
    _automation_instance = instance


def set_session(
    title: str,
    dims: Dict[str, Dict[str, Any]],
    image: str = "",
    bbox: Optional[Dict[str, Any]] = None,
    view: str = "*Isometric",
) -> StudioSession:
    """Replace the studio state and tell every connected page to redraw."""
    _session.title = title
    _session.dims = dims
    _session.image = image
    _session.bbox = bbox
    _session.view = view if view in a2ui.VIEW_NAMES else "*Isometric"
    _session.status = "Drag a slider to rebuild the model in SolidWorks."
    _broadcast([
        {"version": a2ui.A2UI_VERSION, "deleteSurface": {"surfaceId": a2ui.SURFACE_ID}},
        *_session.messages(),
    ])
    return _session


def render_page(include_messages: bool = False) -> str:
    """Self-contained studio HTML: shell + A2UI renderer bundle + connection config."""
    shell = SHELL_PATH.read_text(encoding="utf-8")
    bundle = BUNDLE_PATH.read_text(encoding="utf-8")
    config: Dict[str, Any] = {"bridge": get_bridge_url() or "", "token": _TOKEN}
    if include_messages:
        config["messages"] = _session.messages()
    return (
        shell
        .replace("/*__SW_STUDIO_CONFIG__*/", "window.__SW_STUDIO__ = " + _script_json(config) + ";")
        .replace("/*__SW_STUDIO_BUNDLE__*/", bundle.replace("</script", "<\\/script"))
    )


def _script_json(data: Any) -> str:
    """JSON that is safe to place inside an inline <script> block."""
    return (
        json.dumps(data)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


# ============================================================================
# SolidWorks access (always through the STA worker)
# ============================================================================

def _call(func_name: str, **kwargs: Any) -> Any:
    automation = _automation_instance
    if automation is None:
        raise RuntimeError("SolidWorks automation is not available")
    target = getattr(automation, func_name)
    run_on_sta = getattr(automation, "run_on_sta", None)
    if callable(run_on_sta) and not _is_mock(automation):
        return run_on_sta(target, **kwargs)
    return target(**kwargs)


def _is_mock(obj: Any) -> bool:
    try:
        from unittest.mock import Mock
        return isinstance(obj, Mock)
    except Exception:
        return False


def capture_viewport_data_uri(view_name: str = "*Isometric") -> str:
    """Capture the viewport to a temporary file and return it as a data URI."""
    fd, path = tempfile.mkstemp(prefix="swmcp_studio_", suffix=".png")
    os.close(fd)
    try:
        res = _call("capture_viewport", filepath=path, view_name=view_name)
        if isinstance(res, dict) and res.get("success"):
            saved = (res.get("data") or {}).get("filepath") or path
            if os.path.exists(saved) and os.path.getsize(saved) > 0:
                with open(saved, "rb") as f:
                    return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        logger.warning(f"Studio viewport capture failed: {e}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return ""


def model_bounds() -> Optional[Dict[str, Any]]:
    try:
        res = _call("inspect_model")
        if isinstance(res, dict) and res.get("success"):
            return (res.get("data") or {}).get("dimensions_mm")
    except Exception:
        pass
    return None


# ============================================================================
# A2UI action handling
# ============================================================================

def _status(text: str) -> a2ui.Message:
    _session.status = text
    return a2ui.data_update("/status", text)


def _refresh_view(messages: List[a2ui.Message]) -> None:
    image = capture_viewport_data_uri(_session.view)
    if image:
        _session.image = image
        messages.append(a2ui.data_update("/viewport/image", image))
    bbox = model_bounds()
    if bbox:
        _session.bbox = bbox
        messages.append(a2ui.data_update("/model/info", a2ui.describe_model(bbox, len(_session.dims))))


def _selected_view(context: Dict[str, Any]) -> None:
    view = context.get("view")
    if isinstance(view, list):
        view = view[0] if view else None
    if view in a2ui.VIEW_NAMES:
        _session.view = view


def _apply_dimension(key: str, value: Any) -> List[a2ui.Message]:
    dim = _session.dims.get(key)
    if dim is None:
        return [_status("That dimension is no longer part of this studio. Reopen the studio.")]
    try:
        target = float(value)
    except (TypeError, ValueError):
        return [a2ui.data_update(f"/dims/{key}/value", dim["value"]), _status("Ignored a non-numeric value.")]
    target = min(max(target, dim["min"]), dim["max"])

    previous = dim["value"]
    try:
        res = _call("set_dimension", name=dim["name"], value=target, units=dim["units"])
    except Exception as e:
        res = {"success": False, "message": str(e)}
    if not isinstance(res, dict) or not res.get("success"):
        reason = res.get("message", "rebuild failed") if isinstance(res, dict) else "rebuild failed"
        return [
            a2ui.data_update(f"/dims/{key}/value", previous),
            _status(f"{dim['label']} kept at {previous:g} {dim['units']}: {reason}"),
        ]

    dim["value"] = target
    messages = [
        a2ui.data_update(f"/dims/{key}/value", target),
        a2ui.data_update("/summary", a2ui.build_change_summary(_session.dims)),
        _status(f"{dim['label']} set to {target:g} {dim['units']}."),
    ]
    _refresh_view(messages)
    return messages


def handle_action(action: Dict[str, Any]) -> List[a2ui.Message]:
    """Run one A2UI client action and return the A2UI messages that reflect its outcome."""
    name = action.get("name")
    context = action.get("context") or {}
    with _action_lock:
        _selected_view(context)
        if name == "set_dimension":
            messages = _apply_dimension(str(context.get("key", "")), context.get("value"))
        elif name == "capture":
            messages = [_status(f"Showing {_session.view.lstrip('*')} view.")]
            _refresh_view(messages)
        elif name == "reset":
            messages = []
            for key, dim in _session.dims.items():
                if abs(dim["value"] - dim["original"]) > 1e-9:
                    messages += _apply_dimension(key, dim["original"])
            messages.append(_status("All dimensions restored to their original values."))
        else:
            return [_status(f"Unknown studio action '{name}'.")]
    _broadcast(messages)
    return messages


# ============================================================================
# HTTP server
# ============================================================================

def _broadcast(messages: List[a2ui.Message]) -> None:
    if not messages:
        return
    with _sse_lock:
        for q in list(_sse_clients):
            try:
                q.put_nowait(messages)
            except Exception:
                pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    # Never share the port with another process (SO_REUSEADDR on Windows allows hijacking).
    allow_reuse_address = False


class StudioBridgeHandler(BaseHTTPRequestHandler):
    server_version = "SolidWorksStudio"

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s %s", self.client_address[0], format % args)

    # -- helpers ------------------------------------------------------------

    def _cors(self) -> None:
        # Pages are opened from file://, sandboxed iframes (null origin) and the bridge
        # itself, so the origin cannot be pinned. The token is what authorises a request.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Studio-Token")
        self.send_header("Access-Control-Max-Age", "600")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _json(self, status: int, data: Dict[str, Any]) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

    def _host_is_loopback(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def _authorised(self, query: Dict[str, List[str]]) -> bool:
        supplied = self.headers.get("X-Studio-Token") or (query.get("token") or [""])[0]
        return self._host_is_loopback() and secrets.compare_digest(str(supplied), _TOKEN)

    # -- verbs --------------------------------------------------------------

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        path = url.path.rstrip("/")
        query = parse_qs(url.query)

        if path in ("", "/health"):
            self._json(200, {"status": "ok", "port": _bridge_port})
            return
        if not self._authorised(query):
            self._json(403, {"success": False, "error": "Missing or invalid studio token"})
            return

        if path == "/studio":
            self._send(200, render_page().encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/a2ui":
            self._json(200, {"messages": _session.messages()})
        elif path == "/api/a2ui/events":
            self._serve_events()
        else:
            self._json(404, {"success": False, "error": f"Path not found: {path}"})

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        path = url.path.rstrip("/")
        if not self._authorised(parse_qs(url.query)):
            self._json(403, {"success": False, "error": "Missing or invalid studio token"})
            return
        if path != "/api/a2ui/action":
            self._json(404, {"success": False, "error": f"Path not found: {path}"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_BODY_BYTES:
            self._json(400, {"success": False, "error": "Request body missing or too large"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            action = body["action"]
            if not isinstance(action, dict) or not isinstance(action.get("name"), str):
                raise ValueError("action.name is required")
        except Exception as e:
            self._json(400, {"success": False, "error": f"Not an A2UI client action message: {e}"})
            return

        try:
            self._json(200, {"messages": handle_action(action)})
        except Exception as e:
            logger.error(f"Studio action '{action.get('name')}' failed: {e}")
            self._json(200, {"messages": [_status(f"SolidWorks error: {e}")]})

    def _serve_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()

        client_queue: "queue.Queue" = queue.Queue(maxsize=64)
        with _sse_lock:
            _sse_clients.append(client_queue)
        try:
            while True:
                try:
                    messages = client_queue.get(timeout=15.0)
                    if messages is None:
                        break
                    self.wfile.write(f"data: {json.dumps({'messages': messages})}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            with _sse_lock:
                if client_queue in _sse_clients:
                    _sse_clients.remove(client_queue)


class StudioBridgeServer:
    """Owns the listening socket and its serving thread."""

    def __init__(self, host: str = "127.0.0.1", port_start: int = DEFAULT_PORT_START, port_max: int = DEFAULT_PORT_MAX):
        self.host = host
        self.port_start = port_start
        self.port_max = port_max
        self.port: Optional[int] = None
        self.httpd: Optional[ThreadedHTTPServer] = None
        self._serving = False

    def start(self) -> int:
        last_error: Optional[Exception] = None
        for port in range(self.port_start, self.port_max + 1):
            try:
                self.httpd = ThreadedHTTPServer((self.host, port), StudioBridgeHandler)
                self.port = port
                logger.info(f"Studio bridge listening on http://{self.host}:{port}")
                return port
            except OSError as e:
                last_error = e
        raise RuntimeError(
            f"Could not bind the studio bridge on ports {self.port_start}-{self.port_max}: {last_error}"
        )

    def serve_forever(self) -> None:
        if self.httpd:
            self._serving = True
            try:
                self.httpd.serve_forever()
            finally:
                self._serving = False

    def stop(self) -> None:
        if self.httpd:
            # shutdown() blocks forever unless serve_forever() is actually running.
            if self._serving:
                try:
                    self.httpd.shutdown()
                except Exception:
                    pass
            try:
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None


def start_bridge(port_start: int = DEFAULT_PORT_START, port_max: int = DEFAULT_PORT_MAX) -> int:
    """Start the bridge in a daemon thread (no-op if it is already running). Raises if no port is free."""
    global _server_instance, _server_thread, _bridge_port
    if _server_instance is not None and _bridge_port:
        return _bridge_port
    server = StudioBridgeServer(port_start=port_start, port_max=port_max)
    port = server.start()
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="StudioBridge")
    thread.start()
    _server_instance, _server_thread, _bridge_port = server, thread, port
    return port


def stop_bridge() -> None:
    global _server_instance, _server_thread, _bridge_port
    with _sse_lock:
        for q in list(_sse_clients):
            try:
                q.put_nowait(None)
            except Exception:
                pass
        _sse_clients.clear()
    if _server_instance:
        _server_instance.stop()
    if _server_thread and _server_thread.is_alive():
        _server_thread.join(timeout=2.0)
    _server_instance = None
    _server_thread = None
    _bridge_port = None


def is_bridge_running() -> bool:
    return _server_instance is not None and _bridge_port is not None
