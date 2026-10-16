"""The in-app cookie instructions must match docs/getting-started.md."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_setup_help", ROOT / "scripts" / "build_setup_help.py"
)
assert SPEC and SPEC.loader
build_setup_help = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_setup_help)


def test_generated_setup_help_matches_the_cookie_guide() -> None:
    expected = build_setup_help.render(build_setup_help.SOURCE.read_text(encoding="utf-8"))
    actual = build_setup_help.OUTPUT.read_text(encoding="utf-8")
    assert actual == expected, (
        "tweetnook/web/static/setup-help.json is out of date with docs/getting-started.md; "
        "run `uv run python scripts/build_setup_help.py`."
    )


def test_setup_help_covers_each_browser_and_cookie() -> None:
    data = json.loads(build_setup_help.OUTPUT.read_text(encoding="utf-8"))
    names = [browser["name"] for browser in data["browsers"]]
    assert names[0].startswith("Chrome")
    assert "Firefox" in names and "Safari" in names
    for browser in data["browsers"]:
        code = [
            block["text"]
            for step in browser["steps"]
            for block in step["blocks"]
            if block["type"] == "code"
        ]
        assert {"auth_token", "ct0", "twid"} <= set(code), browser["name"]
    assert "password" in data["caution"][0]["v"]


def test_setup_help_parses_inline_markup_and_rejects_broken_structure() -> None:
    markdown = """> [!CAUTION]
> Keep **these** private.

<details>
<summary><strong>Browser A / Browser B</strong></summary>

Before you start.

### 1. Find `ct0`

Copy its **Value**.

```text
ct0
```

</details>
"""
    data = build_setup_help.build(markdown)
    browser = data["browsers"][0]
    assert browser["name"] == "Browser A / Browser B"
    assert browser["intro"] == [{"type": "p", "inline": [{"t": "text", "v": "Before you start."}]}]
    assert browser["steps"][0]["title"] == [
        {"t": "text", "v": "Find "},
        {"t": "code", "v": "ct0"},
    ]
    assert browser["steps"][0]["blocks"][1] == {"type": "code", "text": "ct0"}

    for broken in (markdown.replace("[!CAUTION]", "[!NOTE]"), markdown.replace("### 1.", "####")):
        try:
            build_setup_help.build(broken)
        except ValueError:
            continue
        raise AssertionError("expected the guide structure to be rejected")
