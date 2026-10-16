from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from tweetnook.tagging import TextTagResult
from tweetnook.tagging_prompts import build_text_system_prompt

ROOT = Path(__file__).resolve().parents[1]


def batch_script(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "demo_tagging_batch", ROOT / "scripts/tag_demo_likes_batch.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "PRIVATE", tmp_path)
    monkeypatch.setattr(module, "WORK", tmp_path / "tagging")
    return module


def test_batch_preparation_scopes_to_likes_and_uses_production_prompt(tmp_path, monkeypatch):
    module = batch_script(tmp_path, monkeypatch)
    fixture = {
        "authors": [{"id": "author", "display_name": "Demo", "username": "demo"}],
        "tweets": [
            {
                "tweet_id": "like",
                "author_id": "author",
                "text": "Liked",
                "collections": ["like"],
                "tags": ["Existing"],
                "article": {"title": "Electronics", "content_text": "Full body"},
            },
            {
                "tweet_id": "bookmark",
                "text": "Not requested",
                "collections": ["bookmark"],
                "tags": [],
            },
            {"tweet_id": "context", "text": "Not requested", "collections": [], "tags": []},
        ],
    }
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo/data.json").write_text(json.dumps(fixture))
    with sqlite3.connect(tmp_path / "archive.db") as connection:
        connection.execute(
            "CREATE TABLE archive (record_type, tweet_id, media_type, local_path, source)"
        )
    module.prepare()
    manifest = module.load("manifest.json")
    assert [row["tweet_id"] for row in manifest["requests"]] == ["like"]
    request = manifest["requests"][0]
    assert request["system_prompt"] == build_text_system_prompt(
        existing_tags=["Existing"], google_search=True
    )
    assert request["schema"] == TextTagResult.model_json_schema()
    assert "Electronics" in request["text"] and "Full body" in request["text"]


def test_submission_uses_true_batch_search_and_refuses_duplicate_billing(tmp_path, monkeypatch):
    module = batch_script(tmp_path, monkeypatch)
    module.save(
        "manifest.json",
        {
            "requests": [
                {
                    "tweet_id": "like",
                    "text": "Post",
                    "media": [],
                    "system_prompt": "Production",
                    "schema": TextTagResult.model_json_schema(),
                }
            ]
        },
    )
    calls = []

    def provider(path, body=None):
        calls.append((path, body))
        if body is None:
            return {"supportedGenerationMethods": ["batchGenerateContent"]}
        return {"name": "batches/test", "metadata": {"state": "BATCH_STATE_PENDING"}}

    monkeypatch.setattr(module, "api", provider)
    module.submit()
    endpoint, payload = calls[-1]
    assert endpoint == "models/gemini-3.6-flash:batchGenerateContent"
    item = payload["batch"]["input_config"]["requests"]["requests"][0]
    assert item["metadata"] == {"key": "like"}
    assert item["request"]["tools"] == [{"googleSearch": {}}]
    assert (
        item["request"]["generationConfig"]["responseSchema"] == TextTagResult.model_json_schema()
    )
    with pytest.raises(RuntimeError, match="duplicate billable"):
        module.submit()
    assert len(calls) == 2


def test_collect_rejects_insufficient_tags_without_inventing_a_result(tmp_path, monkeypatch):
    module = batch_script(tmp_path, monkeypatch)
    module.save("job.json", {"name": "batches/test"})
    module.save("manifest.json", {"requests": [{"tweet_id": "like", "content_type": "text"}]})
    candidate = {"content": {"parts": [{"text": '["OnlyOne"]'}]}}
    job = {
        "done": True,
        "response": {
            "inlinedResponses": {
                "inlinedResponses": [
                    {"metadata": {"key": "like"}, "response": {"candidates": [candidate]}}
                ]
            }
        },
    }
    monkeypatch.setattr(module, "api", lambda path: job)
    with pytest.raises(RuntimeError, match="Expected 1 liked outputs, got 0"):
        module.status(True, None)
    assert not (module.WORK / "validated-results.json").exists()
    candidate["content"]["parts"][0]["text"] = '["SpecificOne", "SpecificTwo"]'
    module.status(True, None)
    assert module.load("validated-results.json")["results"]["like"]["tags"] == [
        "SpecificOne",
        "SpecificTwo",
    ]


def test_batch_cli_help_exposes_actions_and_output(tmp_path, monkeypatch, capsys):
    module = batch_script(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.argv", ["tag_demo_likes_batch.py", "--help"])
    with pytest.raises(SystemExit) as exited:
        module.main()
    assert exited.value.code == 0
    help_text = capsys.readouterr().out
    assert "prepare,submit,status,collect,cleanup" in help_text
    assert "--output" in help_text and "paid batch" in help_text
