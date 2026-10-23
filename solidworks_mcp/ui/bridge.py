"""
SolidWorks MCP - Fast-Bridge HTTP Daemon
----------------------------------------
Lightweight local HTTP server running on 127.0.0.1 (default port: 54321).
Provides sub-100ms slider dimension updates, CORS preflight handling for
sandboxed iframes and Generative UI widgets, single-flight request coalescing,
and real-time viewport image streaming.
"""

import os
import json
import base64
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Optional, Dict, Any

logger = logging.getLogger("SolidWorksMCP.FastBridge")

# Global server state
_server_instance: Optional["FastBridgeServer"] = None
_server_thread: Optional[threading.Thread] = None
_bridge_port: Optional[int] = None
_automation_instance: Any = None
_current_app_html: str = "<html><body><h3>SolidWorks Interactive Studio</h3></body></html>"
_dimension_lock = threading.Lock()


def _dispatch_automation(automation: Any, func_name: str, *args: Any, **kwargs: Any) -> Any:
    """
    Safely invoke an automation method, ensuring COM apartment initialization
    or dispatching to the dedicated STA COM worker thread if available.
    """
    is_mock = False
    try:
        from unittest.mock import Mock, MagicMock
        is_mock = isinstance(automation, (Mock, MagicMock))
    except Exception:
        is_mock = False

    if not is_mock and hasattr(automation, "run_on_sta") and callable(getattr(automation, "run_on_sta")):
        target_fn = getattr(automation, func_name)
        return automation.run_on_sta(target_fn, *args, **kwargs)

    co_init = False
    try:
        import pythoncom
        pythoncom.CoInitialize()
        co_init = True
    except Exception:
        pass

    try:
        target_fn = getattr(automation, func_name)
        return target_fn(*args, **kwargs)
    finally:
        if co_init:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server allowing concurrent reads while writes are coalesced."""
    daemon_threads = True
    allow_reuse_address = True


class FastBridgeHandler(BaseHTTPRequestHandler):
    """HTTP request handler with CORS, JSON API, and STA thread protection."""

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress standard HTTP access logging to keep stdout/stderr clean for MCP."""
        logger.debug("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), format % args))

    def _send_cors_headers(self) -> None:
        """Attach CORS headers to support sandboxed iframes and file:// origins."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
        self.send_header("Access-Control-Max-Age", "86400")

    def _send_json_response(self, status_code: int, data: Dict[str, Any]) -> None:
        """Helper to send JSON response with CORS headers."""
        try:
            payload = json.dumps(data).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        """Handle GET requests: /health, /api/dimensions, /api/app.html."""
        path = self.path.split("?")[0].rstrip("/")

        if path in ("", "/health"):
            doc_title = "None (Disconnected)"
            automation = _get_automation()
            if automation and getattr(automation, "_connected", False):
                try:
                    sw_app = getattr(automation, "_sw_app", None)
                    if sw_app:
                        act_doc = getattr(sw_app, "ActiveDoc", None)
                        if act_doc:
                            title_val = getattr(act_doc, "GetTitle", lambda: getattr(act_doc, "Title", "Active Document"))
                            doc_title = title_val() if callable(title_val) else str(title_val)
                    else:
                        doc_title = "Connected"
                except Exception:
                    doc_title = "Connected"

            self._send_json_response(200, {
                "status": "ok",
                "port": _bridge_port,
                "active_doc": str(doc_title),
            })

        elif path == "/api/dimensions":
            automation = _get_automation()
            if not automation:
                self._send_json_response(503, {"success": False, "error": "SolidWorks automation not connected"})
                return

            try:
                res = _dispatch_automation(automation, "get_dimensions")
                if isinstance(res, dict):
                    data = res.get("data")
                    if isinstance(data, list):
                        dims = data
                    elif isinstance(data, dict):
                        dims = data.get("dimensions", [])
                    elif isinstance(res.get("dimensions"), list):
                        dims = res.get("dimensions", [])
                    else:
                        dims = []
                elif isinstance(res, list):
                    dims = res
                else:
                    dims = []
                self._send_json_response(200, {
                    "success": True,
                    "dimensions": dims,
                    "count": len(dims),
                })
            except Exception as e:
                logger.error(f"Error fetching dimensions: {e}")
                self._send_json_response(500, {"success": False, "error": str(e)})

        elif path == "/api/app.html":
            payload = _current_app_html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(payload)

        else:
            self._send_json_response(404, {"success": False, "error": f"Path not found: {path}"})

    def do_POST(self) -> None:
        """Handle POST requests: /api/set_dimension, /api/capture."""
        path = self.path.split("?")[0].rstrip("/")

        # Read JSON body
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            try:
                body_bytes = self.rfile.read(content_length)
                body = json.loads(body_bytes.decode("utf-8"))
            except Exception as e:
                self._send_json_response(400, {"success": False, "error": f"Invalid JSON body: {e}"})
                return
        else:
            body = {}

        if path == "/api/set_dimension":
            dim_name = body.get("name")
            target_val = body.get("value")
            units = body.get("units", "mm")
            view_name = body.get("view_name", "*Isometric")

            if not dim_name or target_val is None:
                self._send_json_response(400, {"success": False, "error": "Missing required fields: 'name' and 'value'"})
                return

            automation = _get_automation()
            if not automation:
                self._send_json_response(503, {"success": False, "error": "SolidWorks automation not connected"})
                return

            # Single-flight lock prevents concurrent COM apartment reentrancy
            with _dimension_lock:
                prev_val = None
                try:
                    # Retrieve prior dimension value for safe rollback
                    dim_query = _dispatch_automation(automation, "get_dimensions", dimension_name=dim_name)
                    if isinstance(dim_query, dict) and dim_query.get("success"):
                        data = dim_query.get("data")
                        if isinstance(data, list):
                            items = data
                        elif isinstance(data, dict):
                            items = data.get("dimensions", [])
                        elif isinstance(dim_query.get("dimensions"), list):
                            items = dim_query.get("dimensions", [])
                        else:
                            items = []
                        if items and isinstance(items[0], dict):
                            prev_val = items[0].get("value")
                except Exception:
                    pass

                # Execute dimension change on SolidWorks COM
                try:
                    set_res = _dispatch_automation(automation, "set_dimension", name=dim_name, value=float(target_val), units=units)
                except Exception as e:
                    set_res = {"success": False, "message": str(e)}

                if not isinstance(set_res, dict) or not set_res.get("success"):
                    err_msg = set_res.get("message", "Rebuild failed for given dimension") if isinstance(set_res, dict) else "Rebuild failed"
                    # Attempt rollback if prior value is known
                    if prev_val is not None:
                        try:
                            _dispatch_automation(automation, "set_dimension", name=dim_name, value=float(prev_val), units=units)
                        except Exception:
                            pass
                    self._send_json_response(422, {
                        "success": False,
                        "error": err_msg,
                        "revert_value": prev_val,
                    })
                    return

                # Capture fresh viewport snapshot
                image_base64 = _capture_viewport_base64(automation, view_name=view_name)
                bbox_info = _get_model_bounds(automation)

                self._send_json_response(200, {
                    "success": True,
                    "name": dim_name,
                    "value": float(target_val),
                    "units": units,
                    "image_base64": image_base64,
                    "bbox": bbox_info.get("dimensions_mm") if bbox_info else None,
                    "volume": bbox_info.get("volume") if bbox_info else None,
                })

        elif path == "/api/capture":
            view_name = body.get("view_name", "*Isometric")
            automation = _get_automation()
            if not automation:
                self._send_json_response(503, {"success": False, "error": "SolidWorks automation not connected"})
                return

            with _dimension_lock:
                image_base64 = _capture_viewport_base64(automation, view_name=view_name)
                if image_base64:
                    self._send_json_response(200, {
                        "success": True,
                        "view_name": view_name,
                        "image_base64": image_base64,
                    })
                else:
                    self._send_json_response(500, {
                        "success": False,
                        "error": "Failed to capture viewport snapshot",
                    })

        else:
            self._send_json_response(404, {"success": False, "error": f"Path not found: {path}"})


def _get_automation() -> Any:
    """Retrieve the active SolidWorks automation instance."""
    global _automation_instance
    if _automation_instance is not None:
        return _automation_instance
    try:
        from solidworks_mcp.server import sw_automation
        return sw_automation
    except Exception:
        return None


def set_automation_instance(instance: Any) -> None:
    """Inject or override the automation instance (used by tests or server)."""
    global _automation_instance
    _automation_instance = instance


def set_current_app_html(html_content: str) -> None:
    """Update the HTML page served at /api/app.html."""
    global _current_app_html
    _current_app_html = html_content


def _capture_viewport_base64(automation: Any, view_name: str = "*Isometric") -> Optional[str]:
    """Capture viewport snapshot and convert to base64 data URI."""
    try:
        cap_res = _dispatch_automation(automation, "capture_viewport", view_name=view_name)
        if isinstance(cap_res, dict) and cap_res.get("success"):
            data = cap_res.get("data", {})
            filepath = data.get("filepath")
            if filepath and os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode("ascii")
                return f"data:image/png;base64,{encoded}"
    except Exception as e:
        logger.warning(f"FastBridge viewport capture failed: {e}")
    return None


def _get_model_bounds(automation: Any) -> Optional[Dict[str, Any]]:
    """Get active model bounding box and volume info."""
    try:
        insp_res = _dispatch_automation(automation, "inspect_model")
        if isinstance(insp_res, dict) and insp_res.get("success"):
            return insp_res.get("data", {})
    except Exception:
        pass
    return None


class FastBridgeServer:
    """Wrapper managing the background ThreadedHTTPServer lifecycle."""

    def __init__(self, host: str = "127.0.0.1", port_start: int = 54321, port_max: int = 54330):
        self.host = host
        self.port_start = port_start
        self.port_max = port_max
        self.port: Optional[int] = None
        self.httpd: Optional[ThreadedHTTPServer] = None
        self._is_running = False
        self._serving = False

    def start(self) -> int:
        """Bind to the first available port in [port_start, port_max] and start listening."""
        global _bridge_port
        last_error = None
        for port in range(self.port_start, self.port_max + 1):
            try:
                self.httpd = ThreadedHTTPServer((self.host, port), FastBridgeHandler)
                self.port = port
                _bridge_port = port
                self._is_running = True
                logger.info(f"FastBridge HTTP server listening on http://{self.host}:{self.port}")
                return self.port
            except OSError as e:
                last_error = e
                logger.debug(f"Port {port} busy, trying next port...")

        raise RuntimeError(f"Could not bind FastBridge server on ports {self.port_start}-{self.port_max}: {last_error}")

    def serve_forever(self) -> None:
        """Run the server loop (intended for background thread)."""
        if self.httpd:
            self._serving = True
            try:
                self.httpd.serve_forever()
            finally:
                self._serving = False

    def stop(self) -> None:
        """Shut down the server and close the listening socket."""
        global _bridge_port
        if self.httpd:
            self._is_running = False
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
            _bridge_port = None
            logger.info("FastBridge HTTP server stopped")


def start_bridge(port_start: int = 54321, port_max: int = 54330) -> int:
    """Start the FastBridge server in a background daemon thread if not already running."""
    global _server_instance, _server_thread, _bridge_port

    if _server_instance and _server_instance._is_running and _bridge_port:
        return _bridge_port

    _server_instance = FastBridgeServer(port_start=port_start, port_max=port_max)
    bound_port = _server_instance.start()

    _server_thread = threading.Thread(target=_server_instance.serve_forever, daemon=True, name="FastBridgeDaemon")
    _server_thread.start()

    return bound_port


def stop_bridge() -> None:
    """Stop the running FastBridge server."""
    global _server_instance, _server_thread
    if _server_instance:
        _server_instance.stop()
        _server_instance = None
    if _server_thread and _server_thread.is_alive():
        _server_thread.join(timeout=1.0)
        _server_thread = None


def get_bridge_port() -> Optional[int]:
    """Get the currently active bridge port, or None if not running."""
    return _bridge_port


def is_bridge_running() -> bool:
    """Check if the FastBridge server is currently active."""
    return _server_instance is not None and _server_instance._is_running
