"""
MecAgent Out-of-Process .NET 10 Runner & C# SDK Bridge
------------------------------------------------------
Provides direct integration with MecAgent's self-contained .NET 10 STA runner
(`MecAgent.SolidWorks.Runner.exe`), Roslyn C# REPL (`CellSession`), automatic
COM interface cast repair (`CastRepair`), static 12,780-member SolidWorks API
effect classifier (`CellEffectAnalyzer` + `sw-api-effects.json`), runtime
`DocumentFingerprint` verification, multi-angle 7-view viewport stitcher
(`ViewCapture` + `ViewStitcher`), and `SolidWorksLibrary.dll` / `MecAgent.dll`
high-speed inspection & topology controllers (`MCPMethods`).
"""

import atexit
import base64
import ctypes
from ctypes import wintypes
import hashlib
import io
import json
import logging
import msvcrt
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Win32 Job Object Helper (auto-terminates child runner if parent exits)
# ============================================================================

class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9


def _assign_process_to_kill_on_close_job(proc_handle: int) -> Optional[int]:
    """Create an unnamed Win32 Job Object with KILL_ON_JOB_CLOSE and assign proc_handle."""
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        CreateJobObjectW = kernel32.CreateJobObjectW
        CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        CreateJobObjectW.restype = wintypes.HANDLE

        SetInformationJobObject = kernel32.SetInformationJobObject
        SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        SetInformationJobObject.restype = wintypes.BOOL

        AssignProcessToJobObject = kernel32.AssignProcessToJobObject
        AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        AssignProcessToJobObject.restype = wintypes.BOOL

        job = CreateJobObjectW(None, None)
        if not job:
            return None

        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = SetInformationJobObject(
            job,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(job)
            return None

        if not AssignProcessToJobObject(job, wintypes.HANDLE(proc_handle)):
            kernel32.CloseHandle(job)
            return None

        return int(job)
    except Exception as ex:
        logger.debug(f"Could not assign runner process to Win32 Job Object: {ex}")
        return None


# ============================================================================
# Cached C# SDK Dispatcher Bundle (compiled once via ProgramRunner.RunBundle)
# ============================================================================

_SDK_BUNDLE_CSHARP = r"""
using System;
using System.Collections;
using System.Collections.Generic;
using System.Dynamic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using System.Threading.Tasks;
using SolidWorks.Interop.sldworks;

public static class McpSdkBundle
{
    private static readonly object _initLock = new object();
    private static Assembly _swLibAsm;
    private static Type _vsmType;
    private static Type _hostAttachmentType;
    private static Type _partTreeBuilderType;
    private static Type _selCtrlType;
    private static Type _modCtrlType;
    private static Type _connCtrlType;
    private static Type _colorCtrlType;
    private static Type _cadDiscCtrlType;
    private static Type _mcpMethodsType;
    private static object _lastBoundApp;

    private static void EnsureInitialized(string binDir, object app)
    {
        lock (_initLock)
        {
            if (_swLibAsm == null)
            {
                AppDomain.CurrentDomain.AssemblyResolve += (sender, args) =>
                {
                    try
                    {
                        var shortName = new AssemblyName(args.Name).Name + ".dll";
                        var candidate = Path.Combine(binDir, shortName);
                        if (File.Exists(candidate))
                            return Assembly.LoadFrom(candidate);
                    }
                    catch { }
                    return null;
                };

                var newtonsoftPath = Path.Combine(binDir, "Newtonsoft.Json.dll");
                if (File.Exists(newtonsoftPath))
                    Assembly.LoadFrom(newtonsoftPath);

                var swDocMgrPath = Path.Combine(binDir, "SolidWorks.Interop.swdocumentmgr.dll");
                if (File.Exists(swDocMgrPath))
                    Assembly.LoadFrom(swDocMgrPath);

                var hostPath = Path.Combine(binDir, "MecAgent.SolidWorks.Host.dll");
                if (File.Exists(hostPath))
                    Assembly.LoadFrom(hostPath);

                _swLibAsm = Assembly.LoadFrom(Path.Combine(binDir, "SolidWorksLibrary.dll"));

                _vsmType = _swLibAsm.GetType("MecAgent.SolidWorks.VanillaSolidWorksManager");
                _hostAttachmentType = _vsmType?.GetNestedType("HostAttachment", BindingFlags.Public | BindingFlags.NonPublic);
                _partTreeBuilderType = _swLibAsm.GetType("ExternalLibrary.PartFeatureTreeJsonBuilder");
                _selCtrlType = _swLibAsm.GetType("ExternalLibrary.SolidworksSelectionController");
                _modCtrlType = _swLibAsm.GetType("ExternalLibrary.SolidworksModificationController");
                _connCtrlType = _swLibAsm.GetType("ExternalLibrary.SolidworksConnexionController");
                _colorCtrlType = _swLibAsm.GetType("ExternalLibrary.SolidworksColorMappingController");
                _cadDiscCtrlType = _swLibAsm.GetType("ExternalLibrary.CadDataDiscoveryController");
                _mcpMethodsType = _swLibAsm.GetType("MecAgent.MCPMethods");
            }

            if (app != null && !ReferenceEquals(app, _lastBoundApp))
            {
                var setAppMethod = _vsmType?.GetMethod("SetApplication", BindingFlags.Public | BindingFlags.Static);
                setAppMethod?.Invoke(null, new object[] { app });
                _lastBoundApp = app;
            }
        }
    }

    private static object UnwrapTask(object taskObj)
    {
        if (taskObj is Task task)
        {
            task.GetAwaiter().GetResult();
            var type = task.GetType();
            if (type.IsGenericType)
            {
                return type.GetProperty("Result")?.GetValue(task);
            }
            return null;
        }
        return taskObj;
    }

    public static object Run(object app, string jobJson)
    {
        using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(jobJson) ? "{}" : jobJson);
        var root = doc.RootElement;
        string binDir = root.TryGetProperty("binDir", out var bProp) ? (bProp.GetString() ?? "") : "";
        string method = root.TryGetProperty("method", out var mProp) ? (mProp.GetString() ?? "") : "";

        EnsureInitialized(binDir, app);

        switch (method)
        {
            case "getContextFast":
            {
                int depth = root.TryGetProperty("depth", out var dProp) && dProp.ValueKind == JsonValueKind.Number ? dProp.GetInt32() : 2;
                string targetPath = root.TryGetProperty("targetPath", out var tProp) && tProp.ValueKind == JsonValueKind.String ? tProp.GetString() : null;
                string documentName = root.TryGetProperty("documentName", out var docProp) && docProp.ValueKind == JsonValueKind.String ? docProp.GetString() : null;

                var mcp = Activator.CreateInstance(_mcpMethodsType);
                dynamic input = new ExpandoObject();
                input.parameters = new object[] { depth, targetPath, documentName };
                var callMethod = _mcpMethodsType.GetMethod("getContextFast");
                return UnwrapTask(callMethod.Invoke(mcp, new object[] { input }));
            }

            case "getFeatureTreeJson":
            {
                var swApp = app as ISldWorks;
                if (swApp == null)
                    return new { success = false, error = "SolidWorks application is not bound." };

                var swModel = swApp.ActiveDoc as ModelDoc2;
                if (swModel == null)
                    return new { success = false, error = "No active document found in SolidWorks." };

                if (((IModelDoc2)swModel).GetType() != 1)
                    return new { success = false, error = "Active document is not a Part document (.SLDPRT)." };

                var activeView = ((IModelDoc2)swModel).ActiveView as ModelView;
                if (activeView != null)
                    ((IModelView)activeView).EnableGraphicsUpdate = false;
                ((IFeatureManager)((IModelDoc2)swModel).FeatureManager).EnableFeatureTree = false;
                try
                {
                    var buildMethod = _partTreeBuilderType.GetMethod("Build", BindingFlags.Public | BindingFlags.Static);
                    var irDict = buildMethod.Invoke(null, new object[] { swModel });
                    return new { success = true, featureTree = irDict };
                }
                finally
                {
                    ((IFeatureManager)((IModelDoc2)swModel).FeatureManager).EnableFeatureTree = true;
                    if (activeView != null)
                        ((IModelView)activeView).EnableGraphicsUpdate = true;
                }
            }

            case "selectByPath":
            {
                string path = root.TryGetProperty("path", out var pProp) ? (pProp.GetString() ?? "") : "";
                bool multiSelect = root.TryGetProperty("multiSelect", out var msProp) && msProp.ValueKind == JsonValueKind.True;
                int mark = root.TryGetProperty("mark", out var mkProp) && mkProp.ValueKind == JsonValueKind.Number ? mkProp.GetInt32() : 0;

                var ctrl = Activator.CreateInstance(_selCtrlType);
                var m = _selCtrlType.GetMethod("SelectByPath");
                string res = UnwrapTask(m.Invoke(ctrl, new object[] { path, multiSelect, mark })) as string;
                bool ok = !string.IsNullOrEmpty(res) && !res.StartsWith("❌");
                return new
                {
                    success = ok,
                    selectedCount = ok ? 1 : 0,
                    details = res ?? "",
                    path = path,
                    mark = mark,
                    multiSelect = multiSelect,
                    errorMessage = ok ? "" : (res ?? "Selection returned null")
                };
            }

            case "navigate":
            {
                string path = root.TryGetProperty("path", out var pProp) ? (pProp.GetString() ?? "") : "";
                bool showEquations = root.TryGetProperty("showEquations", out var eqProp) && eqProp.ValueKind == JsonValueKind.True;

                var ctrl = Activator.CreateInstance(_selCtrlType);
                var m = _selCtrlType.GetMethod("Navigate");
                string res = UnwrapTask(m.Invoke(ctrl, new object[] { path, showEquations })) as string;
                bool ok = !string.IsNullOrEmpty(res) && !res.StartsWith("❌");
                return new
                {
                    success = ok,
                    path = path,
                    details = res ?? "",
                    errorMessage = ok ? "" : (res ?? "Invalid topology path")
                };
            }

            case "findRelatedCadFiles":
            {
                int maxDrawingsProbed = root.TryGetProperty("maxDrawingsProbed", out var mdProp) && mdProp.ValueKind == JsonValueKind.Number ? mdProp.GetInt32() : 200;
                var ctrl = Activator.CreateInstance(_cadDiscCtrlType);
                var m = _cadDiscCtrlType.GetMethod("FindRelatedFiles");
                return UnwrapTask(m.Invoke(ctrl, new object[] { maxDrawingsProbed }));
            }

            case "getEdgeAnalysis":
            {
                int bodyIndex = root.TryGetProperty("bodyIndex", out var biProp) && biProp.ValueKind == JsonValueKind.Number ? biProp.GetInt32() : 0;
                bool includeAngles = !root.TryGetProperty("includeAngles", out var iaProp) || iaProp.ValueKind != JsonValueKind.False;
                var ctrl = Activator.CreateInstance(_connCtrlType);
                var m = _connCtrlType.GetMethod("GetEdgeAnalysis");
                string res = UnwrapTask(m.Invoke(ctrl, new object[] { bodyIndex, includeAngles })) as string;
                return new
                {
                    success = true,
                    bodyIndex = bodyIndex,
                    edgeAnalysis = res ?? ""
                };
            }

            case "modifyFeatureParameterByPath":
            {
                string path = root.TryGetProperty("path", out var pProp) ? (pProp.GetString() ?? "") : "";
                string parameterId = root.TryGetProperty("parameterId", out var pidProp) ? (pidProp.GetString() ?? "") : "";
                string value = root.TryGetProperty("value", out var vProp) ? (vProp.GetString() ?? "") : "";
                var ctrl = Activator.CreateInstance(_modCtrlType);
                var m = _modCtrlType.GetMethod("ModifyFeatureParameterByPath");
                string res = UnwrapTask(m.Invoke(ctrl, new object[] { path, parameterId, value })) as string;
                bool ok = !string.IsNullOrEmpty(res) && !res.Contains("❌") && !res.Contains("Error");
                return new
                {
                    success = ok,
                    modificationResult = res ?? "",
                    errorMessage = ok ? "" : (res ?? "Modification failed")
                };
            }

            default:
                return new { success = false, error = "Unknown SDK method: " + method };
        }
    }
}
"""

_SDK_BUNDLE_SHA256 = hashlib.sha256(_SDK_BUNDLE_CSHARP.encode("utf-8")).hexdigest()


# ============================================================================
# MecAgentRunnerBridge
# ============================================================================

class MecAgentRunnerBridge:
    """
    Thread-safe Python bridge to `MecAgent.SolidWorks.Runner.exe` over
    inheritable Win32 anonymous pipes speaking newline-delimited JSON.
    """

    def __init__(self, runner_exe_path: Optional[str] = None):
        self._runner_exe = self._resolve_runner_exe(runner_exe_path)
        self._bin_dir = str(Path(self._runner_exe).resolve().parent) if self._runner_exe else ""
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._job_handle: Optional[int] = None
        self._pipe_writer: Optional[io.TextIOWrapper] = None
        self._pipe_reader: Optional[io.TextIOWrapper] = None
        self._ready_event = threading.Event()
        self._ready_info: Dict[str, Any] = {}
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._bound_pid: int = -1
        self._bound_info: Dict[str, Any] = {}
        self._last_com_fault: Optional[Dict[str, Any]] = None

    @staticmethod
    def _resolve_runner_exe(explicit_path: Optional[str] = None) -> str:
        if explicit_path and os.path.exists(explicit_path):
            return str(Path(explicit_path).resolve())

        env_path = os.environ.get("MECAGENT_RUNNER_PATH")
        if env_path and os.path.exists(env_path):
            return str(Path(env_path).resolve())

        # Check bundled runner inside solidworks_mcp/bin/mecagent_runner
        bundled = Path(__file__).resolve().parent.parent / "bin" / "mecagent_runner" / "MecAgent.SolidWorks.Runner.exe"
        if bundled.exists():
            return str(bundled)

        # Fallback to installed mecagent-app in LocalAppData
        local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
        mec_base = Path(local_app_data) / "mecagent-app"
        if mec_base.exists():
            candidates = sorted(
                mec_base.glob("app-*/resources/win-x64/runner/MecAgent.SolidWorks.Runner.exe"),
                reverse=True,
            )
            if candidates:
                return str(candidates[0])

        return str(bundled)

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ensure_started(self, timeout: float = 30.0) -> Dict[str, Any]:
        """Start the out-of-process .NET 10 runner if not already running."""
        with self._lock:
            if self.is_running and self._ready_event.is_set():
                return self._ready_info

            self.shutdown()

            if not os.path.exists(self._runner_exe):
                raise FileNotFoundError(
                    f"MecAgent runner executable not found at: {self._runner_exe}"
                )

            # Create two anonymous pipes:
            # 1) host_to_runner: runner reads r_in, host writes w_in
            # 2) runner_to_host: host reads r_out, runner writes w_out
            r_in, w_in = os.pipe()
            r_out, w_out = os.pipe()

            try:
                os.set_inheritable(r_in, True)
                os.set_inheritable(w_out, True)
                os.set_inheritable(w_in, False)
                os.set_inheritable(r_out, False)

                in_win_handle = msvcrt.get_osfhandle(r_in)
                out_win_handle = msvcrt.get_osfhandle(w_out)
                os.set_handle_inheritable(in_win_handle, True)
                os.set_handle_inheritable(out_win_handle, True)

                bundle_cache_dir = Path(self._bin_dir) / ".bundle_cache"
                bundle_cache_dir.mkdir(parents=True, exist_ok=True)

                cmd_args = [
                    self._runner_exe,
                    "--in",
                    str(in_win_handle),
                    "--out",
                    str(out_win_handle),
                    "--interop-dir",
                    self._bin_dir,
                    "--bundle-cache",
                    str(bundle_cache_dir),
                ]

                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                self._ready_event.clear()
                self._ready_info = {}
                self._bound_pid = -1
                self._bound_info = {}

                self._proc = subprocess.Popen(
                    cmd_args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    close_fds=False,
                    creationflags=creation_flags,
                    cwd=self._bin_dir,
                )
            except Exception:
                for fd in (r_in, w_in, r_out, w_out):
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                raise

            # Close child-side descriptors in parent process
            try:
                os.close(r_in)
            except OSError:
                pass
            try:
                os.close(w_out)
            except OSError:
                pass

            # Assign runner process to a kill-on-close Win32 Job Object
            proc_win_handle = getattr(self._proc, "_handle", None)
            if proc_win_handle:
                self._job_handle = _assign_process_to_kill_on_close_job(int(proc_win_handle))

            self._pipe_writer = io.TextIOWrapper(
                os.fdopen(w_in, "wb", buffering=0),
                encoding="utf-8",
                newline="\n",
                write_through=True,
            )
            self._pipe_reader = io.TextIOWrapper(
                os.fdopen(r_out, "rb", buffering=0),
                encoding="utf-8",
            )

            t_reader = threading.Thread(
                target=self._pipe_reader_loop,
                name="mecagent-runner-pipe-reader",
                daemon=True,
            )
            t_reader.start()

            t_stderr = threading.Thread(
                target=self._stderr_reader_loop,
                name="mecagent-runner-stderr-reader",
                daemon=True,
            )
            t_stderr.start()

        if not self._ready_event.wait(timeout=timeout):
            self.shutdown()
            raise TimeoutError(
                f"MecAgent.SolidWorks.Runner did not emit 'ready' within {timeout}s"
            )

        if self._ready_info.get("error"):
            logger.warning(f"MecAgent runner reported startup error: {self._ready_info.get('error')}")

        return self._ready_info

    def _pipe_reader_loop(self) -> None:
        reader = self._pipe_reader
        if reader is None:
            return
        try:
            for line in reader:
                text = line.strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except Exception as ex:
                    logger.debug(f"[MecAgentRunner] Ignoring non-JSON pipe line: {ex}")
                    continue

                msg_type = msg.get("type")
                if msg_type == "ready":
                    self._ready_info = msg
                    self._ready_event.set()
                    continue

                if msg_type == "event":
                    self._last_com_fault = msg
                    logger.warning(f"[MecAgentRunner] Event: {msg}")
                    continue

                req_id = msg.get("id")
                if req_id:
                    with self._lock:
                        slot = self._pending.get(req_id)
                    if slot is not None:
                        slot["response"] = msg
                        slot["event"].set()
        except Exception as ex:
            logger.debug(f"[MecAgentRunner] Pipe reader loop ended: {ex}")
        finally:
            # Wake up all pending callers if the runner exited unexpectedly
            with self._lock:
                for slot in self._pending.values():
                    if "response" not in slot:
                        slot["error"] = "MecAgent runner process pipe closed unexpectedly"
                    slot["event"].set()

    def _stderr_reader_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw_line in proc.stderr:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if line:
                    logger.debug(f"[MecAgentRunner:stderr] {line}")
        except Exception:
            pass

    def _send_message(self, payload: Dict[str, Any]) -> None:
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        with self._write_lock:
            if self._pipe_writer is None:
                raise RuntimeError("MecAgent runner pipe writer is not open")
            self._pipe_writer.write(line)
            self._pipe_writer.flush()

    def _request(
        self,
        payload: Dict[str, Any],
        timeout: float = 60.0,
        cancel_on_timeout: bool = False,
    ) -> Dict[str, Any]:
        self.ensure_started()
        req_id = payload.get("id") or f"req_{uuid.uuid4().hex[:10]}"
        payload["id"] = req_id

        slot: Dict[str, Any] = {"event": threading.Event()}
        with self._lock:
            self._pending[req_id] = slot

        try:
            self._send_message(payload)
            finished = slot["event"].wait(timeout=timeout)
            if not finished:
                if cancel_on_timeout:
                    try:
                        self._send_message({
                            "type": "cancel",
                            "id": req_id,
                            "abortAfterMs": 1500,
                            "reason": "timeout",
                        })
                        # Give the runner up to 3s to return the cancelled record cleanly
                        slot["event"].wait(timeout=3.0)
                    except Exception:
                        pass
                if "response" not in slot:
                    raise TimeoutError(
                        f"MecAgent runner request '{payload.get('type')}' (id={req_id}) timed out after {timeout}s"
                    )
            if "error" in slot and "response" not in slot:
                raise RuntimeError(slot["error"])
            return slot["response"]
        finally:
            with self._lock:
                self._pending.pop(req_id, None)

    def ensure_bound(
        self,
        pid: Optional[int] = None,
        shared_folder: Optional[str] = None,
        work_dir: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Bind the runner to the active SolidWorks PID (or 0 for headless C# cells).
        """
        self.ensure_started()
        work_directory = work_dir or os.getcwd()
        shared_directory = shared_folder or work_directory

        if pid is not None:
            candidate_pids = [int(pid)]
        else:
            from ..automation.base import find_sldworks_pids
            found = find_sldworks_pids()
            candidate_pids = [int(p) for p in found if int(p) > 0]

        if not force and self._bound_pid > 0 and self._bound_pid in candidate_pids and self._bound_info.get("success"):
            return self._bound_info

        last_error = None
        for cand_pid in candidate_pids:
            resp = self._request(
                {
                    "type": "bind",
                    "id": f"bind_{uuid.uuid4().hex[:8]}",
                    "pid": cand_pid,
                    "installDir": None,
                    "sharedFolder": shared_directory,
                    "workDir": work_directory,
                },
                timeout=30.0,
            )
            if resp.get("success"):
                self._bound_pid = cand_pid
                self._bound_info = resp
                return resp
            last_error = resp.get("error")

        if pid is not None:
            self._bound_pid = -1
            self._bound_info = {
                "type": "bound",
                "success": False,
                "pid": 0,
                "requestedPid": int(pid),
                "error": last_error or f"Could not bind to SolidWorks PID {pid}",
            }
            return self._bound_info

        # Bind headless (pid=0) when no ROT-registered SolidWorks instance is available
        if not force and self._bound_pid == 0 and self._bound_info.get("success"):
            if last_error:
                self._bound_info["bindWarning"] = last_error
            return self._bound_info

        resp = self._request(
            {
                "type": "bind",
                "id": f"bind_{uuid.uuid4().hex[:8]}",
                "pid": 0,
                "installDir": None,
                "sharedFolder": shared_directory,
                "workDir": work_directory,
            },
            timeout=30.0,
        )
        if last_error:
            resp["bindWarning"] = last_error
        self._bound_pid = 0 if resp.get("success") else -1
        self._bound_info = resp
        return resp

    @staticmethod
    def _normalize_cell_globals(code: str) -> str:
        """Allow snake_case aliases (`final_answer`, `shared_path`, `work_path`) alongside PascalCase `ScriptGlobals`."""
        normalized = re.sub(r"(?<![.\w])final_answer\s*\(", "FinalAnswer(", code)
        normalized = re.sub(r"(?<![.\w])shared_path\s*\(", "SharedPath(", normalized)
        normalized = re.sub(r"(?<![.\w])work_path\s*\(", "WorkPath(", normalized)
        return normalized

    def prepare_cell(self, code: str, timeout: float = 30.0) -> Dict[str, Any]:
        """
        Compile a C# cell in the stateful Roslyn `CellSession`, running automatic
        `CS1061` `CastRepair` and static 12,780-member `CellEffectAnalyzer`.
        """
        normalized_code = self._normalize_cell_globals(code)
        resp = self._request(
            {
                "type": "prepareCell",
                "id": f"prep_{uuid.uuid4().hex[:8]}",
                "code": normalized_code,
            },
            timeout=timeout,
        )
        return resp.get("record") or {}

    def discard_prepared(self, prepared_id: str) -> None:
        """Discard a previously prepared cell or macro."""
        try:
            self._send_message({
                "type": "discardPrepared",
                "id": prepared_id,
            })
        except Exception:
            pass

    def run_prepared(
        self,
        prepared_id: str,
        fingerprint: bool = False,
        timeout: float = 120.0,
    ) -> Dict[str, Any]:
        """
        Execute a previously prepared cell or macro on the runner's STA thread,
        optionally verifying `DocumentFingerprint` before/after when classified ReadOnly.
        """
        resp = self._request(
            {
                "type": "runPrepared",
                "id": f"run_{uuid.uuid4().hex[:8]}",
                "preparedId": prepared_id,
                "fingerprint": bool(fingerprint),
            },
            timeout=timeout,
            cancel_on_timeout=True,
        )
        return resp.get("record") or {}

    def execute_csharp(
        self,
        code: str,
        read_only_only: bool = False,
        timeout_ms: int = 120000,
        pid: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Full MecAgent two-phase C# cell execution pipeline:
        1. Ensure runner is bound to the active SolidWorks PID (if running).
        2. `prepareCell`: compile with Roslyn, auto-repair CS1061 COM interface casts,
           and classify every API call against the 12,780-member `sw-api-effects.json` table.
        3. If `read_only_only=True` and the cell is classified as mutating (`readOnly=False`),
           discard the prepared cell and return the static effect findings without executing.
        4. `runPrepared`: run on the isolated STA thread with `DocumentFingerprint`
           verification enabled for read-only cells.
        """
        timeout_s = max(5.0, float(timeout_ms) / 1000.0)
        bind_res = self.ensure_bound(pid=pid)

        prep = self.prepare_cell(code, timeout=min(30.0, timeout_s))
        prep_id = prep.get("id") or ""
        repairs = prep.get("repairs") or []
        effect = prep.get("effect") or {}
        is_read_only = bool(effect.get("readOnly", False))

        if not prep.get("ready"):
            failure = prep.get("failure") or {}
            errors = failure.get("errors") or []
            err_msg = failure.get("error") or "C# compilation failed"
            if errors:
                formatted_errs = [
                    f"Line {e.get('line', 1)}, Col {e.get('column', 1)} [{e.get('id', '')}]: {e.get('message', '')}"
                    for e in errors
                ]
                err_msg = f"{err_msg}:\n" + "\n".join(formatted_errs)
            return {
                "success": False,
                "stage": failure.get("stage", "Compile"),
                "message": err_msg,
                "errors": errors,
                "repairs": repairs,
                "effect": effect,
                "boundPid": bind_res.get("pid", 0),
            }

        if read_only_only and not is_read_only:
            self.discard_prepared(prep_id)
            findings = effect.get("findings") or []
            finding_strs = [
                f"Line {f.get('line', 1)} [{f.get('symbol', '')}]: {f.get('reason', '')}"
                for f in findings
            ]
            return {
                "success": False,
                "stage": "PolicyBlocked",
                "message": (
                    "Execution blocked because `read_only_only=True` and static analysis "
                    "classified this C# cell as mutating:\n" + "\n".join(finding_strs)
                ),
                "effect": effect,
                "repairs": repairs,
                "boundPid": bind_res.get("pid", 0),
            }

        rec = self.run_prepared(
            prepared_id=prep_id,
            fingerprint=is_read_only and (bind_res.get("pid", 0) > 0),
            timeout=timeout_s,
        )

        return_val = None
        return_json = rec.get("returnJson")
        if return_json is not None:
            try:
                return_val = json.loads(return_json)
            except Exception:
                return_val = return_json

        all_repairs = rec.get("repairs") or repairs
        ok = bool(rec.get("success", False))
        msg_parts = []
        if rec.get("stdout"):
            msg_parts.append(f"=== Stdout ===\n{rec['stdout'].rstrip()}")
        if rec.get("finalAnswer"):
            msg_parts.append(f"=== Final Answer ===\n{rec['finalAnswer']}")
        if return_val is not None:
            msg_parts.append(f"=== Return Value ===\n{return_json}")
        if not ok and rec.get("error"):
            msg_parts.append(f"=== Error ===\n{rec['error']}")
        if not msg_parts:
            msg_parts.append("C# cell executed successfully.")

        return {
            "success": ok,
            "stage": rec.get("stage", "Runtime"),
            "message": "\n\n".join(msg_parts),
            "stdout": rec.get("stdout", ""),
            "returnValue": return_val,
            "returnJson": return_json,
            "finalAnswer": rec.get("finalAnswer"),
            "effect": effect,
            "repairs": all_repairs,
            "policyViolation": rec.get("policyViolation"),
            "exception": rec.get("exception"),
            "errors": rec.get("errors") or [],
            "elapsedMs": rec.get("elapsedMs", 0),
            "boundPid": bind_res.get("pid", 0),
            "boundRevision": bind_res.get("revision"),
        }

    def execute_macro(
        self,
        code: str,
        parameters: Optional[List[Any]] = None,
        read_only_only: bool = False,
        timeout_ms: int = 120000,
        pid: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Compile and execute a full `DynamicClass.Execute` C# macro with `CodeTransformer`
        COM timeout wrappers and collectible `AssemblyLoadContext`.
        """
        timeout_s = max(5.0, float(timeout_ms) / 1000.0)
        bind_res = self.ensure_bound(pid=pid)

        prep_resp = self._request(
            {
                "type": "prepareMacro",
                "id": f"prepm_{uuid.uuid4().hex[:8]}",
                "code": code,
                "parameters": parameters or [],
            },
            timeout=min(30.0, timeout_s),
        )
        prep = prep_resp.get("record") or {}
        prep_id = prep.get("id") or ""
        effect = prep.get("effect") or {}
        is_read_only = bool(effect.get("readOnly", False))

        if not prep.get("ready"):
            failure = prep.get("failure") or {}
            errors = failure.get("errors") or []
            err_msg = failure.get("error") or "C# macro compilation failed"
            if errors:
                formatted_errs = [
                    f"Line {e.get('line', 1)}, Col {e.get('column', 1)} [{e.get('id', '')}]: {e.get('message', '')}"
                    for e in errors
                ]
                err_msg = f"{err_msg}:\n" + "\n".join(formatted_errs)
            return {
                "success": False,
                "stage": failure.get("stage", "Compile"),
                "message": err_msg,
                "errors": errors,
                "effect": effect,
                "boundPid": bind_res.get("pid", 0),
            }

        if read_only_only and not is_read_only:
            self.discard_prepared(prep_id)
            findings = effect.get("findings") or []
            finding_strs = [
                f"Line {f.get('line', 1)} [{f.get('symbol', '')}]: {f.get('reason', '')}"
                for f in findings
            ]
            return {
                "success": False,
                "stage": "PolicyBlocked",
                "message": (
                    "Macro blocked because `read_only_only=True` and static analysis "
                    "classified this macro as mutating:\n" + "\n".join(finding_strs)
                ),
                "effect": effect,
                "boundPid": bind_res.get("pid", 0),
            }

        rec = self.run_prepared(
            prepared_id=prep_id,
            fingerprint=is_read_only and (bind_res.get("pid", 0) > 0),
            timeout=timeout_s,
        )

        return_val = None
        return_json = rec.get("returnJson")
        if return_json is not None:
            try:
                return_val = json.loads(return_json)
            except Exception:
                return_val = return_json

        ok = bool(rec.get("success", False))
        return {
            "success": ok,
            "stage": rec.get("stage", "Runtime"),
            "message": rec.get("error") if not ok else (rec.get("stdout") or "C# macro executed successfully."),
            "stdout": rec.get("stdout", ""),
            "returnValue": return_val,
            "returnJson": return_json,
            "effect": effect,
            "policyViolation": rec.get("policyViolation"),
            "exception": rec.get("exception"),
            "errors": rec.get("errors") or [],
            "elapsedMs": rec.get("elapsedMs", 0),
            "boundPid": bind_res.get("pid", 0),
        }

    def capture_views(
        self,
        save_dir: Optional[str] = None,
        timeout: float = 60.0,
        pid: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Capture MecAgent's stitched 7-view overview sheet (1920x720 PNG: Front, Back,
        Left, Right, Top, Bottom + Isometric) and up to 4 zoomed 3D feature-detail
        crops (`detail.png`) via `ViewCapture` + `ViewStitcher`.
        """
        bind_res = self.ensure_bound(pid=pid)
        if not bind_res.get("success") or bind_res.get("pid", 0) <= 0:
            return {
                "success": False,
                "message": f"Cannot capture views: SolidWorks is not bound ({bind_res.get('error') or 'no running instance'}).",
            }

        resp = self._request(
            {
                "type": "captureViews",
                "id": f"views_{uuid.uuid4().hex[:8]}",
            },
            timeout=timeout,
        )

        if not resp.get("success"):
            return {
                "success": False,
                "message": resp.get("error") or "View capture failed",
                "elapsedMs": resp.get("elapsedMs", 0),
            }

        out_dir = Path(save_dir) if save_dir else (Path.cwd() / "artifacts" / "mecagent_views")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)

        overview_b64 = resp.get("overview")
        detail_b64 = resp.get("detail")
        overview_path = None
        detail_path = None

        if overview_b64:
            overview_file = out_dir / f"overview_{ts}.png"
            overview_file.write_bytes(base64.b64decode(overview_b64))
            overview_path = str(overview_file.resolve())

        if detail_b64:
            detail_file = out_dir / f"detail_{ts}.png"
            detail_file.write_bytes(base64.b64decode(detail_b64))
            detail_path = str(detail_file.resolve())

        return {
            "success": True,
            "message": (
                f"Captured 7-view stitched overview ({overview_path})"
                + (f" and feature detail sheet ({detail_path})." if detail_path else ".")
            ),
            "overviewPath": overview_path,
            "detailPath": detail_path,
            "overviewBase64": overview_b64,
            "detailBase64": detail_b64,
            "elapsedMs": resp.get("elapsedMs", 0),
        }

    def invoke_sdk_method(
        self,
        method_name: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 60.0,
        pid: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Invoke a method on `MecAgent.MCPMethods` / `SolidWorksLibrary` inside the
        runner process using a pre-cached `ProgramKind.Bundle` (`McpSdkBundle`),
        preserving the user's `CellSession` state and `SessionEffects`.
        """
        bind_res = self.ensure_bound(pid=pid)
        job = dict(payload or {})
        job["binDir"] = self._bin_dir
        job["method"] = method_name

        resp = self._request(
            {
                "type": "runProgram",
                "id": f"sdk_{uuid.uuid4().hex[:8]}",
                "kind": "Bundle",
                "files": [
                    {
                        "name": "McpSdkBundle.cs",
                        "content": _SDK_BUNDLE_CSHARP,
                    }
                ],
                "entry": {
                    "type": "McpSdkBundle",
                    "method": "Run",
                },
                "parameters": [json.dumps(job)],
                "sha256": _SDK_BUNDLE_SHA256,
                "cacheable": True,
            },
            timeout=timeout,
            cancel_on_timeout=True,
        )

        rec = resp.get("record") or {}
        if not rec.get("success"):
            err = rec.get("error") or "SDK bundle execution failed"
            errors = rec.get("errors") or []
            if errors:
                err += ": " + "; ".join(f"{e.get('id')}: {e.get('message')}" for e in errors)
            return {
                "success": False,
                "error": err,
                "exception": rec.get("exception"),
                "errors": errors,
                "boundPid": bind_res.get("pid", 0),
            }

        ret_json = rec.get("returnJson")
        if ret_json:
            try:
                parsed = json.loads(ret_json)
                if isinstance(parsed, dict):
                    return parsed
                return {"success": True, "result": parsed}
            except Exception:
                return {"success": True, "result": ret_json}

        return {"success": True}

    def reset_session(self) -> Dict[str, Any]:
        """Restart the runner process to reset Roslyn `CellSession` state cleanly."""
        self.shutdown()
        return self.ensure_started()

    def shutdown(self) -> None:
        """Gracefully shut down the runner process and close pipes."""
        with self._lock:
            proc = self._proc
            self._proc = None
            self._ready_event.clear()
            self._bound_pid = -1

            if proc is not None and proc.poll() is None:
                try:
                    self._send_message({"type": "shutdown"})
                except Exception:
                    pass

            if self._pipe_writer is not None:
                try:
                    self._pipe_writer.close()
                except Exception:
                    pass
                self._pipe_writer = None

            if self._pipe_reader is not None:
                try:
                    self._pipe_reader.close()
                except Exception:
                    pass
                self._pipe_reader = None

            if proc is not None:
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

            if self._job_handle is not None and os.name == "nt":
                try:
                    ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self._job_handle))
                except Exception:
                    pass
                self._job_handle = None


# Singleton instance
_runner_bridge: Optional[MecAgentRunnerBridge] = None
_runner_bridge_lock = threading.Lock()


def get_mecagent_runner() -> MecAgentRunnerBridge:
    """Get or create the singleton MecAgentRunnerBridge instance."""
    global _runner_bridge
    with _runner_bridge_lock:
        if _runner_bridge is None:
            _runner_bridge = MecAgentRunnerBridge()
            atexit.register(_runner_bridge.shutdown)
        return _runner_bridge
