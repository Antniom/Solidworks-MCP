"""Every tool call written out in a skill must name a real tool and pass that tool's schema."""

import json
import re
from pathlib import Path

import pytest

from solidworks_mcp import server as srv

TOOL_NAMES = {t.name for t in srv._tool_definitions()}
CALL = re.compile(r"^([a-z][a-z0-9_]+) (\{.*?^\}|\{.*?\})\s*$", re.M | re.S)


def _examples():
    for skill in sorted(Path("skills").glob("*/SKILL.md")):
        text = skill.read_text(encoding="utf-8")
        for block in re.findall(r"```(?:json|jsonc|javascript)?\n(.*?)```", text, re.S):
            block = re.sub(r"^\s*//.*$", "", block, flags=re.M)
            for name, body in CALL.findall(block):
                try:
                    args = json.loads(body)
                except json.JSONDecodeError:
                    continue  # schematic examples such as {"x": float}
                yield pytest.param(name, args, id=f"{skill.parent.name}:{name}:{json.dumps(args)[:40]}")


EXAMPLES = list(_examples())


def test_skills_contain_checkable_examples():
    assert len(EXAMPLES) > 40


@pytest.mark.parametrize("name,args", EXAMPLES)
def test_skill_example_is_a_valid_call(name, args):
    assert name in TOOL_NAMES, f"skills mention a tool that does not exist: {name}"
    assert srv._validate_tool_arguments(name, args) is None, srv._validate_tool_arguments(name, args)


def test_skills_do_not_mention_merged_tool_names():
    for skill in list(Path("skills").glob("*/SKILL.md")) + [Path("rules/AGENTS.md"), Path("README.md")]:
        text = skill.read_text(encoding="utf-8")
        stale = sorted(old for old in srv._MERGED_TOOLS if re.search(rf"\b{old}\b", text))
        assert not stale, f"{skill}: {stale}"
