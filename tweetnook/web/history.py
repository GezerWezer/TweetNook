"""A tweet's availability history for the Web "History" dialog."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from tweetnook.client.timelines import FocalResultKind, parse_tweet_detail_response
from tweetnook.storage.backend import ArchiveStore
from tweetnook.web.availability import UNAVAILABLE_MESSAGES

CHECK_SOURCE = "Availability check"
LOGIN_OPERATIONS = ("TweetDetail", "TweetDetailResurrection", "ThreadExpandDetail")
# A check run records its checks at its start time and writes copies as it goes.
CHECK_CAPTURE_WINDOW = timedelta(hours=6)
COLLECTION_LABELS = {"bookmark": "bookmarks", "like": "likes", "tweet": "your tweets"}
CHECK_LABELS = {
    "available": "Still on X",
    "restored": "Back on X; copy restored",
    "enriched": "Details filled in",
    "protected": "Protected account; not checked",
}


def _reason_message(reason: str | None) -> str:
    return UNAVAILABLE_MESSAGES.get(reason or "", UNAVAILABLE_MESSAGES["unavailable_unknown"])


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _event(at: str | None, event: str, source: str, status: str, detail: str | None = None):
    return {"at": at, "event": event, "source": source, "status": status, "detail": detail}


def _check_events(store: ArchiveStore, tweet_id: str) -> list[dict[str, Any]]:
    events = []
    for row in store.list_tweet_check_history(tweet_id):
        outcome = row["outcome"]
        if outcome in {"tombstone", "not_found"}:
            events.append(
                _event(
                    row["checked_at"],
                    "Missing from X",
                    CHECK_SOURCE,
                    "missing",
                    _reason_message(row.get("reason")),
                )
            )
        else:
            status = "protected" if outcome == "protected" else "available"
            label = CHECK_LABELS.get(outcome, outcome.replace("_", " ").capitalize())
            if row.get("scope") == "quote":
                label += " (checked with the post quoting it)"
            events.append(_event(row["checked_at"], label, CHECK_SOURCE, status))
    return events


def _login_event(tweet_id: str, capture: dict[str, Any]) -> dict[str, Any] | None:
    try:
        payload = json.loads(capture.get("raw_json") or "null")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    at = capture.get("captured_at")
    thread = capture.get("operation") == "ThreadExpandDetail"
    if "http_status" in payload and "data" not in payload:
        return _event(at, "Missing from X", "Your login", "missing", _reason_message("not_found"))
    focal = parse_tweet_detail_response(payload, tweet_id)
    if focal.kind == FocalResultKind.AVAILABLE:
        return _event(at, "Thread saved" if thread else "Fetched", "Your login", "available")
    if focal.kind == FocalResultKind.EXPLICIT_UNAVAILABLE and focal.unavailable is not None:
        return _event(
            at,
            "Missing from X",
            "Your login",
            "missing",
            _reason_message(focal.unavailable.reason),
        )
    return _event(at, "Lookup didn't return the tweet", "Your login", "info")


def _fx_copy_events(
    store: ArchiveStore, tweet_id: str, check_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """FxTwitter copies made outside a check, such as during import enrichment."""

    check_times = [
        moment
        for event in check_events
        if event["status"] in {"available", "missing"}
        and (moment := _parse_time(event["at"])) is not None
    ]
    events = []
    for capture in store.list_target_captures(tweet_id, ("FxTwitterStatus",)):
        captured = _parse_time(capture.get("captured_at"))
        if captured is not None and any(
            start <= captured <= start + CHECK_CAPTURE_WINDOW for start in check_times
        ):
            continue
        try:
            payload = json.loads(capture.get("raw_json") or "null")
        except json.JSONDecodeError:
            continue
        status = payload.get("status") if isinstance(payload, dict) else None
        if isinstance(status, dict) and status.get("type") == "status":
            events.append(
                _event(capture.get("captured_at"), "Details filled in", "FxTwitter", "available")
            )
        else:
            events.append(
                _event(capture.get("captured_at"), "Missing from X", "FxTwitter", "missing")
            )
    return events


def _archive_events(store: ArchiveStore, tweet_id: str) -> list[dict[str, Any]]:
    events = []
    for row in store._rows_for_values(
        "tweet", "tweet_id", [tweet_id], columns=["collection_type", "first_seen_at"]
    ):
        collection = COLLECTION_LABELS.get(row.get("collection_type"), row.get("collection_type"))
        events.append(_event(row.get("first_seen_at"), f"Saved to {collection}", "Archive", "info"))
    if not events:
        for row in store._rows_for_values(
            "tweet_object", "tweet_id", [tweet_id], columns=["first_seen_at"]
        ):
            events.append(
                _event(row.get("first_seen_at"), "Added as a related tweet", "Archive", "info")
            )
    return events


def _note_events(store: ArchiveStore, tweet_id: str) -> list[dict[str, Any]]:
    """Note changes only; their text is in the Community notes dialog."""
    notes = store.list_community_notes(tweet_id)
    added = list(dict.fromkeys(note["first_seen_at"] for note in notes))
    removed = list(dict.fromkeys(note["removed_at"] for note in notes if note.get("removed_at")))
    events = []
    for at in added:
        # A new version replacing the old one in the same check is one change.
        label = "Community note changed" if at in removed else "Community note added"
        events.append(_event(at, label, CHECK_SOURCE, "note"))
    for at in removed:
        if at not in added:
            events.append(_event(at, "Community note removed", CHECK_SOURCE, "note"))
    return events


def tweet_history(store: ArchiveStore, tweet_id: str) -> dict[str, Any]:
    """Every recorded event for one tweet, newest first, plus its current status."""

    check_events = _check_events(store, tweet_id)
    events = [
        *_archive_events(store, tweet_id),
        *check_events,
        *_fx_copy_events(store, tweet_id, check_events),
        *(
            event
            for capture in store.list_target_captures(tweet_id, LOGIN_OPERATIONS)
            if (event := _login_event(tweet_id, capture)) is not None
        ),
        *_note_events(store, tweet_id),
    ]
    events.sort(key=lambda event: event["at"] or "", reverse=True)

    row = store.get_tweet_object_rows([tweet_id]).get(tweet_id) or {}
    missing = row.get("enrichment_state") == "terminal_unavailable"
    last_check = store.get_tweet_check(tweet_id)
    return {
        "tweet_id": tweet_id,
        "status": {
            "state": "missing" if missing else "available",
            "message": _reason_message(row.get("enrichment_reason")) if missing else None,
            "last_checked_at": (last_check or {}).get("checked_at"),
            "next_login_check_at": row.get("enrichment_next_retry_at") if missing else None,
        },
        "events": events,
    }


def tweet_note_history(store: ArchiveStore, tweet_id: str) -> dict[str, Any]:
    """Every version of a tweet's community note, newest first."""

    versions = [
        {
            "text": note["text"],
            "first_seen_at": note["first_seen_at"],
            "last_seen_at": note["last_seen_at"],
            "removed_at": note.get("removed_at"),
            "current": not note.get("removed_at"),
            "source": CHECK_SOURCE,
        }
        for note in store.list_community_notes(tweet_id)
    ]
    stored_text, stored_at = store.stored_community_note(tweet_id)
    if stored_text and all(version["text"] != stored_text for version in versions):
        # Saved with the tweet before any check saw a different note.
        versions.append(
            {
                "text": stored_text,
                "first_seen_at": stored_at,
                "last_seen_at": None,
                "removed_at": min((v["first_seen_at"] for v in versions), default=None),
                "current": not versions,
                "source": "Saved with the tweet",
            }
        )
    versions.sort(key=lambda version: version["first_seen_at"] or "", reverse=True)
    return {"tweet_id": tweet_id, "versions": versions}
