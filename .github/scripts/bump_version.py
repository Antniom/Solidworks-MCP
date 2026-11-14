"""
Bump the plugin's patch version in every file that carries it.

Claude Code only delivers a plugin update when the version in .claude-plugin/plugin.json
changes, so .github/workflows/bump-version.yml runs this on every push to main.

Usage: python bump_version.py            -> bump and print the new version
       python bump_version.py --current  -> print the current version only
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / ".claude-plugin" / "plugin.json"   # the version Claude Code reads
FILES = [
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    ".codex-plugin/plugin.json",
    ".cursor-plugin/plugin.json",
    "plugins/solidworks/plugin.json",
    "solidworks_mcp/__init__.py",
]
PATTERNS = [
    r'("version"\s*:\s*"){old}(")',          # JSON manifests
    r'(__version__\s*=\s*"){old}(")',         # Python package
    r'(^version\s*=\s*"){old}(")',            # pyproject.toml
]


def main() -> None:
    old = json.loads(SOURCE.read_text(encoding="utf-8"))["version"]
    if "--current" in sys.argv:
        print(old)
        return
    major, minor, patch = (int(p) for p in old.split("."))
    new = f"{major}.{minor}.{patch + 1}"
    changed = []
    for rel in FILES:
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        updated = text
        for pat in PATTERNS:
            updated = re.sub(pat.format(old=re.escape(old)), rf"\g<1>{new}\g<2>", updated, flags=re.M)
        if updated != text:
            path.write_text(updated, encoding="utf-8", newline="")
            changed.append(rel)
    if ".claude-plugin/plugin.json" not in changed:
        sys.exit(f"version {old} not found in .claude-plugin/plugin.json")
    print(new)
    print("updated: " + ", ".join(changed), file=sys.stderr)


if __name__ == "__main__":
    main()
