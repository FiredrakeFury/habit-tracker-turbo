"""Release consistency checks: one version everywhere, HACS metadata present."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "habit_log"


def _manifest() -> dict:
    return json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))


def test_versions_match() -> None:
    """manifest.json, const.py and the card must carry the same version."""
    from custom_components.habit_log.const import VERSION

    card = (INTEGRATION / "www" / "habit-log-card.js").read_text(encoding="utf-8")
    match = re.search(r'const VERSION = "([^"]+)";', card)
    assert match, "VERSION constant missing from the card"
    assert _manifest()["version"] == VERSION == match.group(1)


def test_changelog_has_current_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## {_manifest()['version']}\n" in changelog


def test_hacs_metadata() -> None:
    manifest = _manifest()
    for key in ("documentation", "issue_tracker", "codeowners", "name", "version"):
        assert manifest.get(key), f"manifest.json is missing {key}"
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["name"]
    for name in ("icon.png", "icon@2x.png"):
        assert (INTEGRATION / "brand" / name).is_file()
