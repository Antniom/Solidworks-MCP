"""Project board: AGENT_CONTEXT.md parsing, folder scan, status write-back and the server tools."""

from pathlib import Path

import pytest

from solidworks_mcp import server as srv
from solidworks_mcp.core import fs_policy
from solidworks_mcp.ui import project

CONTEXT = """# Pet Feeder — Project Context for CAD Agents
## 0. How to use this file
Read fully before modelling.

## Build order and TRACKLIST

| # | Module / step | Folder | Status | Evidence | Notes |
|---|---|---|---|---|---|
| 1 | Skeleton / layout | 00_Final_Assembly | done | SKEL box 600x1450x650 | |
| 2 | Cabinet | 01_Cabinet | in progress | 12/18 parts | waiting Q3 |
| 3 | Door | 02_Door | blocked (hinge supplier unknown) | | |
| 4 | Electronics | 03_Electronics | todo | | |

## Open questions
- Q3: Which hinge supplier?
- Q4: Paint colour for the cabinet
"""

DECISIONS = """# Decisions
- CAD-001: Origin at the back-left-bottom corner.
- CAD-002: Cabinet wall 1.5 mm sheet.
"""


@pytest.fixture()
def proj(tmp_path, monkeypatch):
    (tmp_path / "AGENT_CONTEXT.md").write_text(CONTEXT, encoding="utf-8")
    (tmp_path / "DECISIONS_LOG.md").write_text(DECISIONS, encoding="utf-8")
    cab = tmp_path / "01_Cabinet"
    cab.mkdir()
    (cab / "Cabinet.SLDASM").write_bytes(b"asm")
    (cab / "Side.SLDPRT").write_bytes(b"prt")
    (cab / "~$Side.SLDPRT").write_bytes(b"lock")
    (tmp_path / "00_Final_Assembly").mkdir()
    (tmp_path / "00_Final_Assembly" / "SKEL.SLDPRT").write_bytes(b"prt")
    policy = fs_policy.FileSystemPolicy(allowed_roots=[str(tmp_path)])
    monkeypatch.setattr(fs_policy, "_GLOBAL_FS_POLICY", policy)
    project.set_automation_instance(None)
    yield tmp_path
    project._state.clear()


def test_board_reads_tracklist_questions_and_decisions(proj):
    res = project.load(str(proj))
    assert res["success"], res
    s = res["data"]
    assert s["title"] == "Pet Feeder"
    assert [m["name"] for m in s["modules"]] == ["Skeleton / layout", "Cabinet", "Door", "Electronics"]
    assert [m["status"] for m in s["modules"]] == ["done", "in progress", "blocked", "todo"]
    assert s["modules"][2]["status_detail"] == "hinge supplier unknown"
    assert s["counts"] == {"todo": 1, "in progress": 1, "blocked": 1, "done": 1}
    cab = s["modules"][1]
    assert cab["files"] == {"parts": 1, "assemblies": 1, "drawings": 0}, "lock files are not parts"
    assert cab["main_file"].endswith("Cabinet.SLDASM")
    assert s["modules"][3]["main_file"] is None
    assert s["open_questions"] == ["Q3: Which hinge supplier?", "Q4: Paint colour for the cabinet"]
    assert s["decisions"]["count"] == 2 and s["decisions"]["recent"][0].startswith("CAD-002")


def test_context_file_in_99_reference_is_found(tmp_path, monkeypatch):
    ref = tmp_path / "99_Reference"
    ref.mkdir()
    (ref / "Feeder_Agent_Context.md").write_text(CONTEXT, encoding="utf-8")
    monkeypatch.setattr(fs_policy, "_GLOBAL_FS_POLICY", fs_policy.FileSystemPolicy(allowed_roots=[str(tmp_path)]))
    assert project.find_context_file(str(tmp_path)).endswith("Feeder_Agent_Context.md")


def test_missing_context_and_disallowed_folder(proj, tmp_path_factory, monkeypatch):
    empty = tmp_path_factory.mktemp("empty")
    monkeypatch.setattr(fs_policy, "_GLOBAL_FS_POLICY", fs_policy.FileSystemPolicy(allowed_roots=[str(empty)]))
    assert "No AGENT_CONTEXT.md" in project.load(str(empty))["message"]
    monkeypatch.setattr(fs_policy.FileSystemPolicy, "is_path_allowed", lambda self, p: False)
    assert "Access denied" in project.load(str(proj))["message"]


def test_set_status_rewrites_only_that_cell(proj):
    project.load(str(proj))
    door = next(m for m in project.get_state()["modules"] if m["name"] == "Door")
    res = project.handle_action("set_status", row=door["row"], status="in progress")
    assert res["success"], res
    text = (proj / "AGENT_CONTEXT.md").read_text(encoding="utf-8")
    assert "| 3 | Door | 02_Door | in progress |  |  |" in text
    assert "| 2 | Cabinet | 01_Cabinet | in progress | 12/18 parts | waiting Q3 |" in text
    assert text.count("\n") == CONTEXT.count("\n"), "no lines added or removed"
    assert res["data"]["counts"]["blocked"] == 0

    res = project.handle_action("set_status", row=door["row"], status="blocked", detail="needs | hinge (PN)")
    assert res["success"]
    assert "| 3 | Door | 02_Door | blocked needs   hinge  PN |" not in (proj / "AGENT_CONTEXT.md").read_text(encoding="utf-8")
    door = next(m for m in res["data"]["modules"] if m["name"] == "Door")
    assert door["status"] == "blocked" and "hinge" in door["status_detail"]


def test_set_status_refuses_stale_rows_and_bad_values(proj):
    project.load(str(proj))
    assert not project.handle_action("set_status", row=1, status="todo")["success"]  # not a tracklist row
    cab = next(m for m in project.get_state()["modules"] if m["name"] == "Cabinet")
    assert not project.handle_action("set_status", row=cab["row"], status="finished")["success"]
    # Someone renamed the row since the board was loaded.
    p = proj / "AGENT_CONTEXT.md"
    p.write_text(p.read_text(encoding="utf-8").replace("| Cabinet |", "| Housing |"), encoding="utf-8")
    res = project.handle_action("set_status", row=cab["row"], status="done")
    assert not res["success"] and "changed" in res["message"]


def test_crlf_files_keep_their_line_endings(proj):
    p = proj / "AGENT_CONTEXT.md"
    p.write_bytes(CONTEXT.replace("\n", "\r\n").encode("utf-8"))
    project.load(str(proj))
    elec = next(m for m in project.get_state()["modules"] if m["name"] == "Electronics")
    assert project.handle_action("set_status", row=elec["row"], status="done")["success"]
    raw = p.read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")


def test_open_only_opens_files_in_the_project(proj, monkeypatch):
    opened = []

    class Auto:
        def open_document(self, filepath):
            opened.append(filepath)
            return {"success": True}

    project.set_automation_instance(Auto())
    project.load(str(proj))
    cab = next(m for m in project.get_state()["modules"] if m["name"] == "Cabinet")
    res = project.handle_action("open", row=cab["row"])
    assert res["success"] and opened[0].endswith("Cabinet.SLDASM")
    elec = next(m for m in project.get_state()["modules"] if m["name"] == "Electronics")
    assert not project.handle_action("open", row=elec["row"])["success"]


def test_server_tools(proj):
    res = srv._dispatch_tool("show_project_board", {"project_folder": str(proj)})
    assert res["success"], res
    assert "- #3 Door: blocked (hinge supplier unknown)" in res["message"]
    assert res["data"]["project"]["title"] == "Pet Feeder"
    res = srv._dispatch_tool("project_action", {"action": "refresh"})
    assert res["success"]
    assert srv._dispatch_tool("project_action", {"action": "delete"})["success"] is False


def test_status_normalisation():
    assert project.normalize_status("**Done** ✅") == ("done", "")
    assert project.normalize_status("WIP") == ("in progress", "")
    assert project.normalize_status("blocked (Q3)") == ("blocked", "Q3")
    assert project.normalize_status("") == ("todo", "")
