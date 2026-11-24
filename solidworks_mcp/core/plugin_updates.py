"""
Plugin auto-update for Claude Code: report its state, turn it on, or record that the user declined.

Claude Code keeps third-party marketplaces off auto-update by default. Turning it on for the marketplace
that installed SolidCADWorks needs two things:

- ``autoUpdate: true`` on the marketplace: in ``~/.claude/settings.json`` (``extraKnownMarketplaces``) and in
  ``~/.claude/plugins/known_marketplaces.json``, where the /plugin toggle stores it;
- ``FORCE_AUTOUPDATE_PLUGINS=1`` in the settings ``env`` block, because the Claude desktop app runs Claude Code
  with ``DISABLE_AUTOUPDATER=1``, which otherwise stops plugin updates too.

The user's answer is remembered in ``%LOCALAPPDATA%/solidworks-mcp/plugin_updates.json`` so they are asked
once. Nothing is changed unless ``enable`` is called, which the skills do only after the user says yes.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PLUGIN_NAME = "solidcadworks"


def _claude_dir() -> Path:
    custom = os.environ.get("CLAUDE_CONFIG_DIR")
    if custom:
        return Path(custom)
    return Path(os.environ.get("USERPROFILE") or os.path.expanduser("~")) / ".claude"


def _state_file() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "solidworks-mcp" / "plugin_updates.json"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: Dict[str, Any], backup_suffix: Optional[str] = None) -> None:
    """Write atomically; keep a one-time backup of the original next to it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup_suffix and path.exists():
        backup = path.with_name(path.name + backup_suffix)
        if not backup.exists():
            shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _marketplace() -> Optional[str]:
    """The marketplace SolidCADWorks was installed from, for a user-scope Claude Code install."""
    registry = _read_json(_claude_dir() / "plugins" / "installed_plugins.json") or {}
    for key, installs in (registry.get("plugins") or {}).items():
        name, _, market = key.partition("@")
        if name != PLUGIN_NAME or not market:
            continue
        for inst in installs if isinstance(installs, list) else [installs]:
            if isinstance(inst, dict) and inst.get("scope", "user") == "user":
                return market
    return None


def _current(market: str) -> Tuple[bool, bool]:
    """(autoUpdate set on the marketplace, FORCE_AUTOUPDATE_PLUGINS set)."""
    settings = _read_json(_claude_dir() / "settings.json") or {}
    known = _read_json(_claude_dir() / "plugins" / "known_marketplaces.json") or {}
    in_settings = bool(((settings.get("extraKnownMarketplaces") or {}).get(market) or {}).get("autoUpdate"))
    in_known = bool((known.get(market) or {}).get("autoUpdate"))
    forced = str((settings.get("env") or {}).get("FORCE_AUTOUPDATE_PLUGINS", "")).strip() in ("1", "true")
    return (in_settings or in_known), forced


def _result(success: bool, message: str, **data: Any) -> Dict[str, Any]:
    return {"success": success, "message": message, "error_code": 0 if success else 1, "data": data}


def status() -> Dict[str, Any]:
    market = _marketplace()
    if not market:
        return _result(True, "SolidCADWorks is not installed as a Claude Code plugin here; plugin auto-update "
                             "does not apply.", state="not_applicable")
    auto, forced = _current(market)
    if auto and forced:
        return _result(True, f"Auto-update is on for the '{market}' marketplace.", state="enabled", marketplace=market)
    decision = (_read_json(_state_file()) or {}).get("decision")
    if decision == "declined":
        return _result(True, "The user chose not to turn on auto-update. Don't ask again unless they bring it up.",
                       state="declined", marketplace=market)
    return _result(True, f"Auto-update is off for the '{market}' marketplace. Ask the user once whether to turn "
                         "it on; then call plugin_updates with action 'enable' or 'decline'.",
                   state="ask_user", marketplace=market, marketplace_auto_update=auto, force_env_set=forced)


def enable() -> Dict[str, Any]:
    market = _marketplace()
    if not market:
        return _result(False, "SolidCADWorks is not installed as a user-scope Claude Code plugin; nothing to change.",
                       state="not_applicable")
    claude = _claude_dir()
    settings_path = claude / "settings.json"
    settings = _read_json(settings_path)
    if settings is None and settings_path.exists():
        return _result(False, f"{settings_path} could not be parsed, so it was left untouched. Fix it, or turn "
                              "auto-update on in /plugin → Marketplaces.", state="ask_user")
    settings = settings or {}
    entry = settings.setdefault("extraKnownMarketplaces", {}).get(market)
    known_path = claude / "plugins" / "known_marketplaces.json"
    known = _read_json(known_path) or {}
    if entry is None:
        # Declare the marketplace with the source Claude Code already recorded for it.
        source = (known.get(market) or {}).get("source")
        if not source:
            return _result(False, f"The '{market}' marketplace is not registered in {known_path}.", state="ask_user")
        entry = settings["extraKnownMarketplaces"][market] = {"source": source}
    entry["autoUpdate"] = True
    settings.setdefault("env", {})["FORCE_AUTOUPDATE_PLUGINS"] = "1"
    _write_json(settings_path, settings, backup_suffix=".before-solidcadworks-autoupdate.bak")
    if market in known:
        known[market]["autoUpdate"] = True
        _write_json(known_path, known, backup_suffix=".before-solidcadworks-autoupdate.bak")
    _write_json(_state_file(), {"decision": "enabled", "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return _result(True, f"Auto-update turned on for the '{market}' marketplace. New SolidCADWorks versions install "
                         "at the start of a later Claude Code session (run /reload-plugins when asked).",
                   state="enabled", marketplace=market, files=[str(settings_path), str(known_path)])


def decline() -> Dict[str, Any]:
    _write_json(_state_file(), {"decision": "declined", "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return _result(True, "Recorded: auto-update stays off and the user won't be asked again. It can be turned on "
                         "later with plugin_updates action 'enable' or in /plugin → Marketplaces.", state="declined")


def plugin_updates(action: str = "status") -> Dict[str, Any]:
    action = (action or "status").lower()
    if action == "enable":
        return enable()
    if action == "decline":
        return decline()
    return status()
