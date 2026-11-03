"""Pure rules for reading Gemini failures and the Paid daily budget."""

from __future__ import annotations

from typing import Any


def provider_error_code(error: BaseException) -> int | None:
    for raw in (
        getattr(error, "status_code", None),
        getattr(error, "code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        if raw is None:
            continue
        try:
            code = int(raw)
        except (ValueError, TypeError):
            continue
        if 400 <= code < 600:
            return code
    return None


def is_retryable_paid_error(error: BaseException, code: int | None) -> bool:
    if code is not None:
        return code in {429, 500, 502, 503, 504}
    if isinstance(error, TimeoutError):
        return True
    text = f"{type(error).__name__}: {error}".upper()
    return any(
        marker in text
        for marker in (
            "429",
            "503",
            "RESOURCE_EXHAUSTED",
            "TIMEOUT",
            "TIMED OUT",
            "UNAVAILABLE",
            "DEADLINE",
        )
    )


def is_retryable_free_error(error: BaseException, code: int | None) -> bool:
    """A provider status decides; message text only speaks for an error that has none."""
    if code is not None:
        return code in {429, 500, 502, 503, 504}
    text = str(error).upper()
    return any(marker in text for marker in ("429", "503", "RESOURCE_EXHAUSTED"))


def is_rejected_free_payload(error: BaseException, code: int | None) -> bool:
    if code is not None:
        return code == 400
    text = str(error).upper()
    return any(marker in text for marker in ("400", "INVALID_ARGUMENT"))


def retry_delay_seconds(attempt_index: int) -> int:
    return 15 * (2**attempt_index)


def safe_error(error: BaseException, api_key: str | None) -> str:
    message = str(error)
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    return message


def budget_pause(tag_config: Any, usage: dict[str, Any]) -> str | None:
    if usage["unreserved"]:
        return "earlier billing uncertainty has no cost reservation"
    if usage["unknown"] and tag_config.unknown_billing_policy == "stop":
        return "billing usage is uncertain (strict policy)"
    if (
        usage["unknown"] - usage.get("capacity_unknown", 0)
        >= tag_config.max_unknown_requests_per_day
    ):
        return "the daily limit on billing-unknown requests was reached"
    if usage["unpriced"]:
        return "an earlier request has no price estimate"
    return None
