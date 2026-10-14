"""
Response Envelope Module
------------------------
Defines typed structured envelopes for all SolidWorks MCP tool operations.
Every tool invocation returns a standardized envelope containing:
- operation_id: unique identifier for tracking/logging
- success: boolean indicating execution outcome
- message: human-readable status message
- data: structured operation payload
- diagnostics: execution timing, environment, and document metadata
- error: structured error object (if failed) with recovery hints
"""

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional

from ..constants import SwErrors

try:
    from mcp.types import CallToolResult, TextContent
    HAS_MCP = True
except ImportError:
    HAS_MCP = False


@dataclass
class ErrorDetail:
    """Structured error descriptor with recovery guidance."""
    code: int
    name: str
    domain: str = "general"
    details: Optional[str] = None
    recovery_hint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "code": self.code,
            "name": self.name,
            "domain": self.domain,
        }
        if self.details:
            result["details"] = self.details
        if self.recovery_hint:
            result["recovery_hint"] = self.recovery_hint
        return result


@dataclass
class DiagnosticsInfo:
    """Runtime diagnostics captured during tool execution."""
    duration_ms: float = 0.0
    sw_version: Optional[str] = None
    active_document: Optional[str] = None
    unit: Optional[str] = None
    thread_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "duration_ms": round(self.duration_ms, 2),
            "sw_version": self.sw_version,
            "active_document": self.active_document,
            "unit": self.unit,
            "thread_id": self.thread_id,
        }


@dataclass
class ResponseEnvelope:
    """Typed structured envelope returned by all SolidWorks MCP tools."""
    success: bool
    message: str
    operation_id: str = field(default_factory=lambda: f"op_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}")
    data: Dict[str, Any] = field(default_factory=dict)
    diagnostics: DiagnosticsInfo = field(default_factory=DiagnosticsInfo)
    error: Optional[ErrorDetail] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert envelope to serializable dictionary."""
        out = {
            "operation_id": self.operation_id,
            "success": self.success,
            "message": self.message,
            "diagnostics": self.diagnostics.to_dict(),
            "data": self.data,
        }
        if self.error:
            out["error"] = self.error.to_dict()
        return out

    def to_json(self, indent: int = 2) -> str:
        """Serialize envelope to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def to_mcp_text(self) -> str:
        """Format envelope into readable text format for MCP text responses."""
        status = "SUCCESS" if self.success else "ERROR"
        lines = [f"[{status}] {self.message}"]
        lines.append(f"Operation ID: {self.operation_id}")

        if not self.success and self.error:
            lines.append(f"Error: {self.error.code} ({self.error.name}) [Domain: {self.error.domain}]")
            if self.error.details:
                lines.append(f"Details: {self.error.details}")
            if self.error.recovery_hint:
                lines.append(f"Recovery Hint: {self.error.recovery_hint}")

        if self.data:
            lines.append("Data: " + json.dumps(self.data, indent=2, default=str))

        diag = self.diagnostics.to_dict()
        if diag.get("duration_ms"):
            lines.append(f"Duration: {diag['duration_ms']} ms")

        return "\n".join(lines)

    def to_call_tool_result(self) -> Any:
        """Convert envelope into an MCP SDK 2.x CallToolResult."""
        if not HAS_MCP:
            raise RuntimeError("MCP SDK is not installed in the current environment.")
        return CallToolResult(
            content=[TextContent(type="text", text=self.to_mcp_text())],
            is_error=not self.success,
            meta=self.to_dict(),
        )


def success_envelope(
    message: str,
    data: Optional[Dict[str, Any]] = None,
    duration_ms: float = 0.0,
    sw_version: Optional[str] = None,
    active_document: Optional[str] = None,
    unit: Optional[str] = None,
    operation_id: Optional[str] = None,
) -> ResponseEnvelope:
    """Factory helper to construct a successful ResponseEnvelope."""
    op_id = operation_id or f"op_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
    return ResponseEnvelope(
        success=True,
        message=message,
        operation_id=op_id,
        data=data or {},
        diagnostics=DiagnosticsInfo(
            duration_ms=duration_ms,
            sw_version=sw_version,
            active_document=active_document,
            unit=unit,
        ),
        error=None,
    )


def error_envelope(
    message: str,
    error_code: int = int(SwErrors.swGeneralError),
    error_name: str = "swGeneralError",
    domain: str = "general",
    details: Optional[str] = None,
    recovery_hint: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    duration_ms: float = 0.0,
    sw_version: Optional[str] = None,
    active_document: Optional[str] = None,
    operation_id: Optional[str] = None,
) -> ResponseEnvelope:
    """Factory helper to construct a failed ResponseEnvelope."""
    op_id = operation_id or f"op_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
    return ResponseEnvelope(
        success=False,
        message=message,
        operation_id=op_id,
        data=data or {},
        diagnostics=DiagnosticsInfo(
            duration_ms=duration_ms,
            sw_version=sw_version,
            active_document=active_document,
        ),
        error=ErrorDetail(
            code=error_code,
            name=error_name,
            domain=domain,
            details=details,
            recovery_hint=recovery_hint,
        ),
    )


def create_envelope(result_dict: Dict[str, Any], operation_id: Optional[str] = None) -> ResponseEnvelope:
    """Convert a legacy result dictionary into a typed ResponseEnvelope."""
    success = bool(result_dict.get("success", False))
    message = str(result_dict.get("message", ""))
    data = result_dict.get("data", {})
    if not isinstance(data, dict):
        data = {"value": data}

    if success:
        return success_envelope(message=message, data=data, operation_id=operation_id)

    code = result_dict.get("error_code", int(SwErrors.swGeneralError))
    name = result_dict.get("error_name", "swGeneralError")
    return error_envelope(
        message=message,
        error_code=code,
        error_name=name,
        data=data,
        operation_id=operation_id,
    )
