"""plugin_updates: asks once, turns Claude Code plugin auto-update on only when told to, and never guesses."""

import json
from pathlib import Path

import pytest

from solidworks_mcp.core import plugin_updates as pu

MARKET = "my-market"
SOURCE = {"source": "git", "url": "https://example.com/market.git"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return tmp_path


def _install(home: Path, scope="user", settings=None):
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {f"solidcadworks@{MARKET}": [{"scope": scope, "version": "5.0.44"}]}}), encoding="utf-8")
    (plugins / "known_marketplaces.json").write_text(json.dumps(
        {MARKET: {"source": SOURCE, "installLocation": "x", "lastUpdated": "y"}}), encoding="utf-8")
    if settings is not None:
        (home / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


def _settings(home):
    return json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))


def test_not_a_claude_code_plugin_install(home):
    assert pu.plugin_updates("status")["data"]["state"] == "not_applicable"
    assert pu.plugin_updates("enable")["success"] is False


def test_project_scope_install_is_not_applicable(home):
    _install(home, scope="project")
    assert pu.plugin_updates("status")["data"]["state"] == "not_applicable"


def test_status_asks_and_changes_nothing(home):
    _install(home, settings={"outputStyle": "Concise"})
    before = (home / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert pu.plugin_updates("status")["data"]["state"] == "ask_user"
    assert (home / ".claude" / "settings.json").read_text(encoding="utf-8") == before


def test_enable_sets_both_places_and_keeps_other_settings(home):
    _install(home, settings={"outputStyle": "Concise", "env": {"OTHER": "1"},
                             "extraKnownMarketplaces": {MARKET: {"source": SOURCE}}})
    assert pu.plugin_updates("enable")["success"] is True

    s = _settings(home)
    assert s["outputStyle"] == "Concise" and s["env"] == {"OTHER": "1", "FORCE_AUTOUPDATE_PLUGINS": "1"}
    assert s["extraKnownMarketplaces"][MARKET] == {"source": SOURCE, "autoUpdate": True}
    known = json.loads((home / ".claude" / "plugins" / "known_marketplaces.json").read_text(encoding="utf-8"))
    assert known[MARKET]["autoUpdate"] is True and known[MARKET]["installLocation"] == "x"
    assert (home / ".claude" / "settings.json.before-solidcadworks-autoupdate.bak").exists()
    assert pu.plugin_updates("status")["data"]["state"] == "enabled"


def test_enable_declares_the_marketplace_when_settings_lack_it(home):
    _install(home)  # no settings.json at all
    assert pu.plugin_updates("enable")["success"] is True
    assert _settings(home)["extraKnownMarketplaces"][MARKET] == {"source": SOURCE, "autoUpdate": True}


def test_unparseable_settings_are_left_alone(home):
    _install(home)
    broken = '{"outputStyle": "Concise",  <-- truncated'
    (home / ".claude" / "settings.json").write_text(broken, encoding="utf-8")
    assert pu.plugin_updates("enable")["success"] is False
    assert (home / ".claude" / "settings.json").read_text(encoding="utf-8") == broken


def test_decline_is_remembered(home):
    _install(home, settings={})
    assert pu.plugin_updates("decline")["data"]["state"] == "declined"
    assert pu.plugin_updates("status")["data"]["state"] == "declined"
    assert "env" not in _settings(home)


def test_tool_is_registered_and_skills_mention_it():
    from solidworks_mcp import server as srv
    assert "plugin_updates" in {t.name for t in srv._tool_definitions()}
    assert "plugin_updates" in srv._TOOL_HANDLERS
    assert "plugin_updates" in srv.SERVER_INSTRUCTIONS
    for skill in Path("skills").glob("*/SKILL.md"):
        assert "plugin_updates" in skill.read_text(encoding="utf-8"), skill
