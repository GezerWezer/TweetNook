"""The usage log stays authoritative through failed summaries and torn appends."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from tweetnook.tagging.accounting import AIUsageLedger, failed_usage_record


def unknown_record(*, reserve: str = "0.02", searches: int | None = None):
    return failed_usage_record(
        run_id="run",
        request_id="request",
        api_mode="paid",
        model="gemini-3.6-flash",
        service_tier="flex",
        content_type="text",
        tweet_ids=["1"],
        latency_ms=1,
        search_enabled=searches is not None,
        search_reservation_consumed=searches,
        status="billing_unknown",
        reserved_cost_usd=reserve,
    )


def only_log(ledger: AIUsageLedger):
    return next(ledger.root.glob("????-??.jsonl"))


def fail_next_summary_publish(monkeypatch, ledger: AIUsageLedger) -> None:
    publish = ledger._atomic_summary
    calls = []

    def fail_once(summary):
        if not calls:
            calls.append(True)
            raise OSError("summary replace refused")
        publish(summary)

    monkeypatch.setattr(ledger, "_atomic_summary", fail_once)


def test_durable_append_with_a_failed_summary_counts_after_reopening(monkeypatch, tmp_path):
    ledger = AIUsageLedger(tmp_path)
    ledger.append(unknown_record(reserve="0.01"))
    fail_next_summary_publish(monkeypatch, ledger)

    with pytest.raises(OSError, match="summary replace refused"):
        ledger.append(unknown_record(reserve="0.02", searches=5))

    reopened = AIUsageLedger(tmp_path)
    budget = reopened.daily_budget_usage()
    assert budget["unknown"] == 2
    assert budget["reserved"] == Decimal("0.03")
    assert reopened.monthly_search_count() == 5


def test_later_append_does_not_perpetuate_a_missed_record(monkeypatch, tmp_path):
    ledger = AIUsageLedger(tmp_path)
    ledger.append(unknown_record(reserve="0.01"))
    fail_next_summary_publish(monkeypatch, ledger)
    with pytest.raises(OSError):
        ledger.append(unknown_record(reserve="0.02", searches=5))

    AIUsageLedger(tmp_path).append(unknown_record(reserve="0.04"))

    budget = AIUsageLedger(tmp_path).daily_budget_usage()
    assert budget["unknown"] == 3
    assert budget["reserved"] == Decimal("0.07")
    assert AIUsageLedger(tmp_path).monthly_search_count() == 5


def test_append_after_a_rebuild_skipped_its_log_does_not_hide_that_log(monkeypatch, tmp_path):
    ledger = AIUsageLedger(tmp_path)
    ledger.append(unknown_record(reserve="0.01"))
    ledger.append(unknown_record(reserve="0.02", searches=5))
    ledger.summary_path.unlink()
    read_bytes = Path.read_bytes
    failures = []

    def fail_once(path: Path) -> bytes:
        if path.suffix == ".jsonl" and not failures:
            failures.append(path)
            raise OSError("transient read failure")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_once)

    ledger.append(unknown_record(reserve="0.04"))

    assert failures
    reopened = AIUsageLedger(tmp_path)
    budget = reopened.daily_budget_usage()
    assert budget["unknown"] == 3
    assert budget["reserved"] == Decimal("0.07")
    assert reopened.monthly_search_count() == 5


def test_current_summary_is_not_rebuilt_on_each_read(monkeypatch, tmp_path):
    ledger = AIUsageLedger(tmp_path)
    ledger.append(unknown_record())
    monkeypatch.setattr(ledger, "_rebuild_locked", lambda: pytest.fail("trusted summary rebuilt"))

    assert ledger.daily_budget_usage()["unknown"] == 1


@pytest.mark.parametrize(
    "torn",
    [b'{"timestamp":', b'{"note":"caf\xc3'],
    ids=["partial-json", "partial-utf8"],
)
def test_torn_tail_does_not_absorb_the_next_record(tmp_path, torn):
    ledger = AIUsageLedger(tmp_path)
    ledger.append(unknown_record(reserve="0.01"))
    with only_log(ledger).open("ab") as handle:
        handle.write(torn)

    ledger.append(unknown_record(reserve="0.02"))

    data = only_log(ledger).read_bytes()
    assert torn + b"\n" in data
    rebuilt = AIUsageLedger(tmp_path).rebuild()
    assert rebuilt["lifetime"]["requests"] == 2
    budget = AIUsageLedger(tmp_path).daily_budget_usage()
    assert budget["unknown"] == 2
    assert budget["reserved"] == Decimal("0.03")


def test_complete_record_without_a_newline_is_kept_and_counted(tmp_path):
    ledger = AIUsageLedger(tmp_path)
    ledger.append(unknown_record(reserve="0.01"))
    log = only_log(ledger)
    log.write_bytes(log.read_bytes().rstrip(b"\n"))

    ledger.append(unknown_record(reserve="0.02"))

    assert len(log.read_bytes().splitlines()) == 2
    assert AIUsageLedger(tmp_path).daily_budget_usage()["reserved"] == Decimal("0.03")
    assert AIUsageLedger(tmp_path).rebuild()["lifetime"]["requests"] == 2


def test_ordinary_appends_add_no_blank_lines(tmp_path):
    ledger = AIUsageLedger(tmp_path)
    ledger.append(unknown_record())
    ledger.append(unknown_record())

    assert b"\n\n" not in only_log(ledger).read_bytes()
    assert ledger.rebuild()["lifetime"]["requests"] == 2
