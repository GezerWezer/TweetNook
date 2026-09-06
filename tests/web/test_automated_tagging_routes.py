from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from tweetnook import tagging
from tweetnook.automated_tagging import GeminiModel
from tweetnook.config import AppConfig, TaggingConfig
from tweetnook.web.routes import automated_tagging as routes


def model(model_id: str = "gemini-3.6-flash") -> GeminiModel:
    return GeminiModel(
        id=model_id,
        name="Gemini 3.6 Flash",
        version="2026-08",
        input_token_limit=1_000_000,
        output_token_limit=64_000,
        supported_methods=("generateContent",),
        thinking=True,
    )


def test_settings_are_masked_and_report_optional_install_state(
    monkeypatch, make_web_client, tmp_path
) -> None:
    config = AppConfig(tagging=TaggingConfig(api_key="secret"))
    monkeypatch.setattr(
        routes,
        "_load",
        lambda: (config, SimpleNamespace(ai_usage_dir=tmp_path / "ai-usage")),
    )
    monkeypatch.setattr(routes, "automated_tagging_installed", lambda: True)
    client = make_web_client(routes.router)

    response = client.get("/api/automated-tagging")

    assert response.status_code == 200
    assert response.json()["installed"] is True
    payload = response.json()
    assert payload["values"]["api_key"] == "********"
    assert payload["accounting"]["today_cost_usd"] == "0"
    assert payload["accounting"]["today_requests"] == 0
    assert payload["accounting"]["tweet_types"] == {}
    assert len(payload["accounting"]["spend_history"]) == 365
    assert payload["accounting"]["lifetime_spend_history"] == []
    assert set(payload["accounting"]["spend_history"][-1]) == {
        "date",
        "estimated_cost_usd",
        "requests",
        "unknown_cost_requests",
        "unpriced_requests",
    }


def test_paid_settings_require_limit_or_unlimited(monkeypatch, make_web_client) -> None:
    config = AppConfig(tagging=TaggingConfig(api_key="secret"))
    monkeypatch.setattr(routes, "_load", lambda: (config, SimpleNamespace()))
    monkeypatch.setattr(routes, "automated_tagging_installed", lambda: True)
    client = make_web_client(routes.router)

    response = client.put(
        "/api/automated-tagging",
        json={"values": {"enabled": True, "api_mode": "paid", "api_key": "********"}},
    )

    assert response.status_code == 400
    assert "spending limit or Unlimited" in response.json()["detail"]


def test_catalog_uses_live_metadata_without_static_model_table(
    monkeypatch, make_web_client
) -> None:
    config = AppConfig(tagging=TaggingConfig(api_key="secret"))
    monkeypatch.setattr(routes, "_load", lambda: (config, SimpleNamespace()))
    monkeypatch.setattr(routes, "automated_tagging_installed", lambda: True)
    monkeypatch.setattr(
        routes,
        "list_models",
        lambda api_key: [model(), model("gemini-3.5-flash-lite")],
    )
    client = make_web_client(routes.router)

    paid = client.post(
        "/api/automated-tagging/models",
        json={"api_mode": "paid", "processing_tier": "flex"},
    )
    free = client.post(
        "/api/automated-tagging/models",
        json={"api_mode": "free", "processing_tier": "standard"},
    )

    assert paid.status_code == 200
    assert paid.json()["models"][0]["input_token_limit"] == 1_000_000
    assert {item["id"] for item in paid.json()["models"]} == {
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
    }
    assert free.status_code == 200
    assert {item["id"] for item in free.json()["models"]} == {
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
    }


def test_page_is_not_available_when_extra_is_missing(monkeypatch, make_web_client) -> None:
    monkeypatch.setattr(routes, "automated_tagging_installed", lambda: False)
    client = make_web_client(routes.router)
    response = client.post("/api/automated-tagging/models", json={})
    assert response.status_code == 404


def test_tweet_picker_finds_exact_status_url_and_reports_media(make_web_client) -> None:
    store = SimpleNamespace(conn=sqlite3.connect(":memory:", check_same_thread=False))
    store.conn.row_factory = sqlite3.Row
    store.conn.execute(
        "CREATE TABLE archive (record_type TEXT, tweet_id TEXT, author_display_name TEXT, "
        "author_username TEXT, text TEXT)"
    )
    store.conn.execute("CREATE INDEX idx_archive_tweet_id ON archive(tweet_id)")
    store.conn.execute(
        "INSERT INTO archive VALUES ('tweet_object', '42', 'Alice', 'alice', 'A test tweet')"
    )
    store.conn.execute("INSERT INTO archive (record_type, tweet_id) VALUES ('media', '42')")
    client = make_web_client(routes.router, store=store)

    response = client.get(
        "/api/automated-tagging/tweets",
        params={"q": "https://x.com/alice/status/42"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "tweets": [
            {
                "tweet_id": "42",
                "author_display_name": "Alice",
                "author_username": "alice",
                "text": "A test tweet",
                "has_media": True,
            }
        ]
    }


def test_test_run_uses_unsaved_settings_and_returns_preview(
    monkeypatch, make_web_client, tmp_path
) -> None:
    saved = AppConfig(tagging=TaggingConfig(api_key="saved-secret", model="saved-model"))
    store = SimpleNamespace()
    observed = {}

    async def fake_tag_media_tweets(**kwargs):
        observed["config"] = kwargs["config"]
        observed["dry_run"] = kwargs["dry_run"]
        kwargs["preview_results"].append(
            tagging.TaggingPreview(
                id="42",
                content_type="text",
                model="unsaved-model",
                tweet_type="Standalone",
                author_display_name="Alice",
                author_username="alice",
                text="A test tweet",
                tags=["Specific One", "Specific Two"],
            )
        )
        kwargs["console"].print("Generated a preview")
        return 1

    monkeypatch.setattr(
        routes,
        "_load",
        lambda: (
            saved,
            SimpleNamespace(data_dir=tmp_path, ai_usage_dir=tmp_path / "ai-usage"),
        ),
    )
    monkeypatch.setattr(routes, "automated_tagging_installed", lambda: True)
    monkeypatch.setattr(tagging, "tag_media_tweets", fake_tag_media_tweets)
    client = make_web_client(routes.router, store=store)

    response = client.post(
        "/api/automated-tagging/test",
        json={
            "tweet_id": "42",
            "values": {
                "api_key": "********",
                "model": "unsaved-model",
                "tagging_context": ["Unsaved context"],
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["result"]["tags"] == ["Specific One", "Specific Two"]
    assert "Generated a preview" in response.json()["output"]
    assert observed["dry_run"] is True
    assert observed["config"].tagging.api_key == "saved-secret"
    assert observed["config"].tagging.model == "unsaved-model"
    assert observed["config"].tagging.tagging_context == ["Unsaved context"]
    assert saved.tagging.model == "saved-model"
