"""Offline tests for core/issue_reporter.py (report_issue tool): redaction, schema limits, queueing."""

import json
import re
from pathlib import Path

import pytest

from solidworks_mcp.core import issue_reporter as rep


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("USERNAME", "jdoe")
    monkeypatch.setattr(rep, "_sent_this_session", 0)
    monkeypatch.setattr(rep, "_seen_this_session", set())
    yield


def test_redact_paths_user_and_email():
    text = ('open C:\\Users\\jdoe\\OneDrive - Acme\\Client X\\bracket.SLDPRT failed; '
            'json "C:\\\\Users\\\\jdoe\\\\Proj\\\\a.SLDASM"; unc \\\\srv\\share\\cad\\b.step; '
            'fwd C:/Users/jdoe/x/c.png; by jdoe <jdoe@acme.com>; Antonio stays')
    out = rep.redact(text)
    assert "Client X" not in out and "OneDrive" not in out and "srv" not in out
    assert "bracket.SLDPRT" in out and "a.SLDASM" in out and "b.step" in out and "c.png" in out
    assert "jdoe" not in out and "<user>" in out and "<email>" in out
    assert "Antonio stays" in out


def test_build_report_matches_firestore_rules():
    rules = Path("firebase/firestore.rules").read_text(encoding="utf-8")
    allowed = set(re.search(r"hasOnly\(\[(.*?)\]\)", rules, re.S).group(1).replace("'", "").replace("\n", "")
                  .replace(" ", "").split(","))
    doc = rep.build_report({"kind": "missing_tool", "title": "need a loft", "output": "x" * 60000,
                            "arguments": {"a": 1}, "reproducible": "maybe"}, "5.0.99", "33.5.0")
    assert set(doc) <= allowed
    assert set(rep.TEXT_FIELDS) | {"kind", "reproducible", "server_version", "sw_version", "os", "python",
                                   "install_id", "report_id", "client_time", "source"} == allowed
    assert len(doc["output"]) <= 50000 and doc["output"].endswith("chars]")
    assert doc["reproducible"] == "unknown" and doc["kind"] == "missing_tool"
    for field, limit in rep.TEXT_FIELDS.items():
        assert f"optStr(d, '{field}', {limit})" in rules or field == "title"
    with pytest.raises(ValueError):
        rep.build_report({"kind": "spam", "title": "x"}, "1", None)
    with pytest.raises(ValueError):
        rep.build_report({"title": "  "}, "1", None)


def test_submit_sends_saves_and_dedupes(monkeypatch):
    posted = []
    monkeypatch.setattr(rep, "_post", lambda doc: posted.append(doc))
    r1 = rep.submit({"title": "select ignores component", "tool": "select"}, "5.0.99")
    assert r1["sent"] and len(posted) == 1 and Path(r1["local_copy"]).exists()
    r2 = rep.submit({"title": "Select ignores component", "tool": "select"}, "5.0.99")
    assert r2.get("duplicate") and len(posted) == 1


def test_offline_report_is_queued_then_flushed(monkeypatch):
    def fail(doc):
        raise OSError("offline")
    monkeypatch.setattr(rep, "_post", fail)
    r1 = rep.submit({"title": "first"}, "5.0.99")
    assert not r1["sent"] and "outbox" in r1["local_copy"] and Path(r1["local_copy"]).exists()
    posted = []
    monkeypatch.setattr(rep, "_post", lambda doc: posted.append(doc))
    r2 = rep.submit({"title": "second"}, "5.0.99")
    assert r2["sent"] and r2["flushed_from_outbox"] == 1 and len(posted) == 2
    assert not list((Path(r1["local_copy"]).parent).glob("*.json"))


def test_reporting_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("SW_MCP_REPORTING", "0")
    monkeypatch.setattr(rep, "_post", lambda doc: pytest.fail("must not send"))
    r = rep.submit({"title": "local only"}, "5.0.99")
    assert not r["sent"] and Path(r["local_copy"]).exists()
    assert json.loads(Path(r["local_copy"]).read_text(encoding="utf-8"))["title"] == "local only"
