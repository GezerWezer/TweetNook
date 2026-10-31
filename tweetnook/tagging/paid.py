"""One Paid tweet's bounded attempts, each settled once in the usage ledger."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from rich.console import Console
from rich.text import Text

from tweetnook.jobs.resource_failure import raise_if_storage_exhausted
from tweetnook.tagging.accounting import (
    SEARCH_MONTHLY_ALLOWANCE,
    AIUsageLedger,
    SearchAccounting,
    failed_usage_record,
    has_usable_interaction_usage,
    interaction_search_accounting,
    new_request_id,
    usage_from_interaction,
)
from tweetnook.tagging.failures import (
    budget_pause,
    is_retryable_paid_error,
    provider_error_code,
    retry_delay_seconds,
    safe_error,
)
from tweetnook.tagging.preparation import PreparedBatch
from tweetnook.tagging.pricing import (
    load_openrouter_pricing,
    minimum_input_cost_usd,
    pricing_multiplier,
)
from tweetnook.tagging.results import MediaTagResult, TextTagResult
from tweetnook.tagging.run import TaggingOutcome, TaggingRun, announce_batch, persist, system_prompt

PAID_MAX_PROVIDER_FAILURES = 5
PAID_PROVIDER_FAILURE_WINDOW_SECONDS = 15 * 60


SEARCH_ATTEMPT_SAFETY_RESERVATION = 5


@dataclass(frozen=True, slots=True)
class SearchReservation:
    """In-flight safety budget for one provider attempt.

    This is an application reservation, not a claim about how many searches a
    Gemini request can perform.  The provider's authoritative count replaces it
    when the response is available.
    """

    amount: int = SEARCH_ATTEMPT_SAFETY_RESERVATION


@dataclass(slots=True)
class SearchPolicy:
    """Search budget for one run. Nothing here awaits, so settling can't be interrupted."""

    ledger: AIUsageLedger
    reserve: int
    enabled: bool
    in_flight: int = 0
    unavailable_warning_emitted: bool = False
    limit_warning_emitted: bool = False
    unknown_policy: str = "disable"
    max_unknown_attempts: int = 3
    unknown_attempts: int = 0

    def reserve_attempt(self) -> tuple[SearchReservation | None, str | None]:
        if not self.enabled:
            return None, "accounting" if self.unavailable_warning_emitted else None
        used = self.ledger.monthly_search_count()
        if (
            used + self.in_flight + SEARCH_ATTEMPT_SAFETY_RESERVATION
            >= SEARCH_MONTHLY_ALLOWANCE - self.reserve
        ):
            first = not self.limit_warning_emitted
            self.limit_warning_emitted = True
            return None, "limit" if first else None
        reservation = SearchReservation()
        self.in_flight += reservation.amount
        return reservation, None

    def release(self, reservation: SearchReservation | None) -> None:
        if reservation is not None:
            self.in_flight = max(self.in_flight - reservation.amount, 0)

    def mark_unknown(self, reservation: SearchReservation | None) -> bool:
        """Consume an estimated reservation; cap tolerated unknown attempts."""

        if reservation is None:
            return False
        self.release(reservation)
        if not self.enabled:
            return False
        self.unknown_attempts += 1
        if self.unknown_policy == "reserve" and self.unknown_attempts < self.max_unknown_attempts:
            return False
        self.enabled = False
        first = not self.unavailable_warning_emitted
        self.unavailable_warning_emitted = True
        return first


@dataclass(slots=True)
class PaidRunState:
    attempts: int = 0
    stop_reason: str | None = None
    provider_failures: int = 0
    provider_failure_started: float | None = None

    def provider_pause_reason(self) -> str | None:
        if self.provider_failures >= PAID_MAX_PROVIDER_FAILURES:
            return (
                "Gemini remained unavailable after five consecutive request failures; retry later"
            )
        if (
            self.provider_failure_started is not None
            and time.monotonic() - self.provider_failure_started
            >= PAID_PROVIDER_FAILURE_WINDOW_SECONDS
        ):
            return "Gemini remained unavailable for 15 minutes; retry later"
        return None


def is_capacity_response(code: int | None, failure: Any, message: str) -> bool:
    """Recognize an explicit overload rejection, without evidence of generation.

    A generic 503 or a client timeout remains ambiguous. Even a capacity
    rejection keeps its unknown billing and reserve; only retry policy differs.
    """
    if code != 503:
        return False
    for name in ("usage", "id", "outputs", "output", "output_text", "steps"):
        value = failure.get(name) if isinstance(failure, dict) else getattr(failure, name, None)
        if value is not None:
            return False
    error = failure.get("error") if isinstance(failure, dict) else None
    return (
        isinstance(error, dict) and error.get("code") == "service_unavailable"
    ) or "currently experiencing high demand" in message.casefold()


def _interaction_thinking_level(level: str) -> str:
    return "minimal" if level == "none" else level


def _part_from_uri() -> Any | None:
    try:  # Optional extra: importing this module must remain safe in base installations.
        from google.genai import types
    except ImportError:
        return None
    return getattr(getattr(types, "Part", None), "from_uri", None)


async def count_request_tokens(
    client: Any,
    *,
    model: str,
    system_prompt: str,
    interaction_input: list[Any],
    search_enabled: bool = False,
    console: Console | None = None,
    api_key: str | None = None,
) -> tuple[int | None, bool]:
    """Count prompt/media input; subsequent Search/tool expansion is uncertain."""

    counter = getattr(getattr(client, "models", None), "count_tokens", None)
    if not callable(counter):
        if console is not None:
            console.print(
                Text("Token preflight unavailable: SDK has no token counter.", style="yellow")
            )
        return None, False
    # Developer API countTokens rejects CountTokensConfig.system_instruction.
    # Include the prompt as content for this estimate; the Interactions request
    # still sends its real system instruction separately.
    contents: list[Any] = [system_prompt] if system_prompt else []
    for item in interaction_input:
        if isinstance(item, str):
            contents.append(item)
            continue
        if not isinstance(item, dict):
            contents.append(item)
            continue
        item_type = str(item.get("type") or "")
        if item_type == "text":
            contents.append(str(item.get("text") or ""))
            continue
        uri = item.get("uri")
        if uri:
            part_factory = _part_from_uri()
            if callable(part_factory):
                try:
                    contents.append(
                        part_factory(
                            file_uri=str(uri),
                            mime_type=str(item.get("mime_type") or "application/octet-stream"),
                        )
                    )
                    continue
                except Exception:
                    pass
            # Token preflight is telemetry only.  A URI string is still a
            # GenerateContent-compatible scalar when a test/fallback SDK has
            # no Part factory; never pass Interactions dictionaries through.
            contents.append(str(uri))
    kwargs: dict[str, Any] = {"model": model, "contents": contents}
    try:
        response = await asyncio.to_thread(counter, **kwargs)
    except Exception as error:
        if console is not None:
            console.print(Text(f"Token preflight unavailable: {safe_error(error, api_key)}"))
        return None, False
    total = getattr(response, "total_tokens", None)
    if total is None:
        total = getattr(response, "totalTokenCount", None)
    try:
        return max(int(total), 1), True
    except (TypeError, ValueError):
        if console is not None:
            console.print(Text("Token preflight unavailable: invalid token count.", style="yellow"))
        return None, False


async def create_interaction(client: Any, **kwargs: Any) -> Any:
    aio = getattr(client, "aio", None)
    if aio is not None and getattr(aio, "interactions", None) is not None:
        if hasattr(aio.interactions, "sdk_configuration"):
            # Newer SDKs retry internally even with HttpRetryOptions(attempts=1).
            # Own retries here so every HTTP attempt has separate accounting.
            aio.interactions.sdk_configuration.retry_config = None
        return await aio.interactions.create(**kwargs)
    if hasattr(client.interactions, "sdk_configuration"):
        client.interactions.sdk_configuration.retry_config = None
    return await asyncio.to_thread(client.interactions.create, **kwargs)


@dataclass(slots=True)
class _PaidAttempt:
    """One provider attempt, from preflight to settlement."""

    number: int
    started: float
    search_reservation: SearchReservation | None = None
    use_search: bool = False
    provider_started: bool = False
    search_settled: bool = False
    pricing: Any = None
    preflight_input_tokens: int | None = None
    reserved_cost: Decimal = Decimal("0")
    phase: str = "preflight"


@dataclass(slots=True)
class PaidRequest:
    """One tweet's Paid request and its bounded attempts, each settled in the ledger once."""

    run: TaggingRun
    client: Any
    batch: PreparedBatch
    existing_tags: Sequence[str]
    request_id: str = field(default_factory=new_request_id)
    metadata: dict[str, Any] = field(init=False)
    daily_limit: Decimal | None = field(init=False)

    def __post_init__(self) -> None:
        tag_config = self.run.tag_config
        self.metadata = self.batch.request_metadata(
            existing_tag_count=len(self.existing_tags), tag_config=tag_config
        )
        self.daily_limit = (
            None
            if tag_config.unlimited_spend or tag_config.daily_spend_limit_usd is None
            else Decimal(str(tag_config.daily_spend_limit_usd))
        )

    @property
    def tweet_ids(self) -> list[str]:
        return self.batch.tweet_ids

    def _pause(self, reason: str) -> None:
        self.run.paid_state.stop_reason = reason
        self.run.console.print(Text(f"Paid tagging paused: {reason}.", style="yellow"))

    def _append_usage(self, record: Any) -> None:
        try:
            self.run.ledger.append(record)
        except Exception:
            self._pause("the usage ledger could not be saved")
            raise

    def _pause_for_provider(self) -> bool:
        reason = self.run.paid_state.provider_pause_reason()
        if reason:
            self._pause(reason)
        return bool(reason)

    async def _preflight(self, attempt: _PaidAttempt) -> dict[str, Any] | None:
        """Build the attempt's request, or None when the run must pause instead."""
        run = self.run
        tag_config = run.tag_config
        if self._pause_for_provider():
            return None
        budget = run.ledger.daily_budget_usage()
        if self.daily_limit is not None:
            reason = budget_pause(tag_config, budget)
            if reason:
                self._pause(reason)
                return None
            if budget["spend"] + budget["reserved"] >= self.daily_limit:
                self._pause("daily estimated spend and uncertainty reserves reached the limit")
                return None
        if tag_config.google_search:
            attempt.search_reservation, reason = run.search_policy.reserve_attempt()
            attempt.use_search = attempt.search_reservation is not None
            if reason:
                run.console.print(
                    Text(f"Google Search disabled for this attempt: {reason}.", style="yellow")
                )
        prompt = system_prompt(run, self.batch, self.existing_tags, search=attempt.use_search)
        interaction_input = self.batch.interaction_input()
        attempt.pricing = await asyncio.to_thread(
            load_openrouter_pricing, run.paths.ai_usage_dir, run.model_name
        )
        attempt.preflight_input_tokens, _ = await count_request_tokens(
            self.client,
            model=run.model_name,
            system_prompt=prompt,
            interaction_input=interaction_input,
            search_enabled=attempt.use_search,
            console=run.console,
            api_key=tag_config.api_key,
        )
        attempt.reserved_cost = self._reserved_cost(attempt)
        if self.daily_limit is not None:
            if attempt.pricing is None:
                self._pause("current pricing is unavailable")
                return None
            if budget["spend"] + budget["reserved"] + attempt.reserved_cost > self.daily_limit:
                self._pause("the next request's cost allowance would exceed the daily budget")
                return None
        result_schema = MediaTagResult if self.batch.content_type == "media" else TextTagResult
        request: dict[str, Any] = {
            "model": run.model_name,
            "input": interaction_input,
            "store": False,
            "system_instruction": prompt,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": result_schema.model_json_schema(),
            },
            "generation_config": {
                "max_output_tokens": 2048,
                "thinking_level": _interaction_thinking_level(tag_config.thinking_level),
            },
            "service_tier": tag_config.processing_tier,
        }
        if tag_config.processing_tier == "flex":
            request["timeout"] = 900.0
        if attempt.use_search:
            request["tools"] = [{"type": "google_search"}]
        # Preflight can consume the remaining failure interval.
        return None if self._pause_for_provider() else request

    def _reserved_cost(self, attempt: _PaidAttempt) -> Decimal:
        # This is an explicit local uncertainty allowance, not a claimed provider billing
        # cap. Never estimate old unknown records retroactively, or turn their actual cost
        # into zero.
        tag_config = self.run.tag_config
        reserved = Decimal(str(tag_config.unknown_request_reserve_usd))
        if attempt.pricing is None or attempt.preflight_input_tokens is None:
            return reserved
        multiplier = pricing_multiplier(tag_config.processing_tier)
        estimate = (
            minimum_input_cost_usd(
                attempt.pricing, attempt.preflight_input_tokens, multiplier=multiplier
            )
            + Decimal(2048)
            * max(attempt.pricing.output_per_token, attempt.pricing.thinking_per_token)
            * multiplier
        )
        return max(reserved, estimate)

    def _settlement_fields(
        self, attempt: _PaidAttempt, *, search_consumed: int | None
    ) -> dict[str, Any]:
        tag_config = self.run.tag_config
        return {
            "run_id": self.run.run_identifier,
            "request_id": self.request_id,
            "service_tier": tag_config.processing_tier,
            "content_type": self.batch.content_type,
            "tweet_ids": self.tweet_ids,
            "search_enabled": attempt.use_search,
            "latency_ms": round((time.monotonic() - attempt.started) * 1000),
            "search_reservation_consumed": search_consumed,
            "attempt": attempt.number,
            "thinking_level": tag_config.thinking_level,
            "request_metadata": self.metadata,
            "estimate": {"input_tokens": attempt.preflight_input_tokens},
            "reserved_cost_usd": str(attempt.reserved_cost),
            "pricing": attempt.pricing,
            "preflight_input_tokens": attempt.preflight_input_tokens,
        }

    def _settle_search(self, attempt: _PaidAttempt, search: SearchAccounting) -> None:
        policy = self.run.search_policy
        if search.reliable:
            policy.release(attempt.search_reservation)
        else:
            disabled = policy.mark_unknown(attempt.search_reservation)
            self.run.console.print(
                Text(
                    "Search usage is unknown; retained an estimated five-query reserve."
                    + (" Search disabled for the remainder of this run." if disabled else ""),
                    style="yellow",
                )
            )
        attempt.search_settled = True

    def _settle_response(self, attempt: _PaidAttempt, response: Any) -> None:
        run = self.run
        run.paid_state.provider_failures = 0
        run.paid_state.provider_failure_started = None
        reservation = attempt.search_reservation
        reliable = interaction_search_accounting(response, search_enabled=attempt.use_search)
        record, search = usage_from_interaction(
            response,
            requested_model=run.model_name,
            **self._settlement_fields(
                attempt,
                search_consumed=(
                    reservation.amount
                    if reservation is not None and not reliable.reliable
                    else None
                ),
            ),
        )
        self._append_usage(record)
        self._settle_search(attempt, search)
        if record.status == "billing_unknown":
            run.console.print(
                Text(
                    f"Billing usage missing for tweet {self.tweet_ids[0]}, "
                    f"attempt {attempt.number}; retained ${attempt.reserved_cost} as an "
                    "estimated uncertainty reserve.",
                    style="yellow",
                )
            )
            if self.daily_limit is not None:
                reason = budget_pause(run.tag_config, run.ledger.daily_budget_usage())
                if reason:
                    self._pause(reason)

    def _settle_failure(self, attempt: _PaidAttempt, error: Exception) -> float | None:
        """Record a failed attempt; returns the delay before retrying, or None to give up."""
        run = self.run
        tag_config = run.tag_config
        code = provider_error_code(error)
        retryable = is_retryable_paid_error(error, code)
        message = safe_error(error, tag_config.api_key)
        run.console.print(
            Text(
                f"Gemini {attempt.phase} failed for tweet {self.tweet_ids[0]}, "
                f"attempt {attempt.number}/5: "
                f"{type(error).__name__} (HTTP {code or 'unknown'}): {message}",
                style="yellow",
            )
        )
        if run.paid_state.stop_reason == "the usage ledger could not be saved":
            return None
        failure = (
            getattr(error, "interaction", None)
            or getattr(error, "response", None)
            or getattr(error, "result", None)
        )
        if callable(getattr(failure, "json", None)):
            try:
                body = failure.json()
                if isinstance(body, dict):
                    failure = body
            except (ValueError, TypeError):
                pass
        usage = (
            failure.get("usage") if isinstance(failure, dict) else getattr(failure, "usage", None)
        )
        has_usage = has_usable_interaction_usage(usage)
        # Explicit validation/auth/rate rejections with no evidence of generation are
        # distinct from timeouts/5xx ambiguity.
        interaction_id = (
            failure.get("id") if isinstance(failure, dict) else getattr(failure, "id", None)
        )
        rejected = code in {400, 401, 403, 404, 422, 429} and not (
            usage is not None or interaction_id
        )
        billing_unknown = attempt.provider_started and not has_usage and not rejected
        capacity_failure = billing_unknown and is_capacity_response(code, failure, message)
        search = (
            interaction_search_accounting(failure, search_enabled=attempt.use_search)
            if attempt.provider_started and not rejected
            else SearchAccounting(0, "disabled")
        )
        reservation = attempt.search_reservation
        common = self._settlement_fields(
            attempt,
            search_consumed=(
                reservation.amount if reservation is not None and not search.reliable else None
            ),
        )
        if has_usage:
            record, _ = usage_from_interaction(failure, requested_model=run.model_name, **common)
            record = replace(record, status="failed")
        else:
            record = failed_usage_record(
                api_mode="paid",
                model=run.model_name,
                search=search,
                status="billing_unknown" if billing_unknown else "failed",
                pricing_multiplier=pricing_multiplier(tag_config.processing_tier),
                **common,
            )
            if not billing_unknown:
                record = replace(record, cost_usd="0", estimated_cost_usd="0")
        self._append_usage(
            replace(
                record,
                error_type=type(error).__name__,
                error_code=code,
                error_message=message,
                failure_phase=attempt.phase,
                failure_kind="capacity" if capacity_failure else None,
            )
        )
        # Provider availability errors do not identify a bad tweet. Leave the queue eligible
        # for the next run and reserve candidate backoff for item-specific failures.
        if not run.dry_run and not retryable and code not in {401, 403}:
            run.store.defer_tagging_failure(self.tweet_ids)
        self._settle_search(attempt, search)
        if code in {401, 403}:
            self._pause("Gemini rejected authentication or access")
            return None
        if billing_unknown and self.daily_limit is not None:
            reason = budget_pause(tag_config, run.ledger.daily_budget_usage())
            if reason:
                self._pause(reason)
                return None
        if retryable and attempt.provider_started:
            state = run.paid_state
            state.provider_failures += 1
            if state.provider_failure_started is None:
                state.provider_failure_started = attempt.started
            if self._pause_for_provider():
                return None
        if not retryable or attempt.number >= 5:
            return None
        return self._retry_delay(attempt, capacity_failure=capacity_failure)

    def _settle_cancelled(self, attempt: _PaidAttempt) -> None:
        """A request cancelled in flight may still be billed, so it keeps its uncertainty."""
        run = self.run
        search = interaction_search_accounting(None, search_enabled=attempt.use_search)
        reservation = attempt.search_reservation
        record = failed_usage_record(
            api_mode="paid",
            model=run.model_name,
            search=search,
            status="billing_unknown",
            pricing_multiplier=pricing_multiplier(run.tag_config.processing_tier),
            **self._settlement_fields(
                attempt,
                search_consumed=(
                    reservation.amount if reservation is not None and not search.reliable else None
                ),
            ),
        )
        try:
            run.ledger.append(
                replace(
                    record,
                    error_type="CancelledError",
                    error_message="cancelled while the request was in flight",
                    failure_phase=attempt.phase,
                )
            )
        except Exception as error:
            # The cancellation still propagates, and this attempt's Search stays settled.
            run.console.print(
                Text(
                    "A cancelled request could not be saved to the usage ledger: "
                    f"{safe_error(error, run.tag_config.api_key)}",
                    style="yellow",
                )
            )
        self._settle_search(attempt, search)

    def _retry_delay(self, attempt: _PaidAttempt, *, capacity_failure: bool) -> float:
        delay: float = retry_delay_seconds(attempt.number - 1)
        if capacity_failure:
            delay *= random.uniform(0.8, 1.2)
        failure_started = self.run.paid_state.provider_failure_started
        if failure_started is not None:
            remaining = PAID_PROVIDER_FAILURE_WINDOW_SECONDS - (time.monotonic() - failure_started)
            delay = max(0, min(delay, remaining))
        return delay

    async def send(self) -> Any | None:
        """Send until a response arrives; None when the attempts gave up or the run paused."""
        run = self.run
        for index in range(5):
            attempt = _PaidAttempt(number=index + 1, started=time.monotonic())
            try:
                request = await self._preflight(attempt)
                if request is None:
                    return None
                attempt.phase = "interaction"
                attempt.provider_started = True
                run.paid_state.attempts += 1
                response = await create_interaction(self.client, **request)
                # Settlement doesn't await, so a cancellation lands before it or after it.
                self._settle_response(attempt, response)
                return response  # Keep useful output even if its billing is unknown.
            except asyncio.CancelledError:
                if attempt.provider_started and not attempt.search_settled:
                    self._settle_cancelled(attempt)
                raise
            except Exception as error:
                raise_if_storage_exhausted(error)
                delay = self._settle_failure(attempt, error)
                if delay is None:
                    return None
                run.console.print(
                    Text(
                        f"Gemini API busy. Retrying in {delay:g} seconds "
                        f"(Attempt {attempt.number}/5)...",
                        style="yellow",
                    )
                )
                await asyncio.sleep(delay)
            finally:
                # Also release reservations on a pricing/budget early return.
                if not attempt.search_settled:
                    run.search_policy.release(attempt.search_reservation)
        return None


async def tag_paid_tweet(
    run: TaggingRun, client: Any, batch: PreparedBatch, existing_tags: Sequence[str]
) -> TaggingOutcome:
    announce_batch(run, batch)
    response = await PaidRequest(run, client, batch, existing_tags).send()

    def finished(tagged: int = 0) -> TaggingOutcome:
        # Output that arrived before a pause is still saved and counted.
        reason = run.paid_state.stop_reason
        if reason:
            return TaggingOutcome.paused(reason, tagged=tagged)
        return TaggingOutcome.from_results(tagged)

    if response is None:
        return finished()
    response_text = getattr(response, "output_text", None)
    if not response_text:
        run.console.print("[red]Gemini returned an empty response.[/red]")
        if not run.dry_run:
            run.store.defer_tagging_failure(batch.tweet_ids)
        return finished()
    model = MediaTagResult if batch.content_type == "media" else TextTagResult
    try:
        parsed = [(batch.tweet_ids[0], model.model_validate_json(response_text))]
    except (ValidationError, ValueError, TypeError) as error:
        run.console.print(
            "[red]Gemini returned invalid structured output: "
            f"{safe_error(error, run.tag_config.api_key)}[/red]"
        )
        if not run.dry_run:
            run.store.defer_tagging_failure(batch.tweet_ids)
        return finished()
    return finished(persist(run, batch, parsed, existing_tags))
