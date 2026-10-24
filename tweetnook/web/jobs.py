"""The Web jobs list: every job the CLI can run, its last and next run, and run options.

Each option maps to one CLI flag, so a run started from the Web takes exactly the
arguments the same command takes in a terminal.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from tweetnook.activity_history import list_runs, read_run
from tweetnook.client.fxtwitter import fx_enabled
from tweetnook.config import AppConfig

OptionKind = Literal["flag", "negflag", "level", "int", "text", "archive"]


@dataclass(frozen=True, slots=True)
class JobOption:
    """One CLI flag.

    ``flag`` adds the flag when on; ``negflag`` adds it when off (``--no-enrich``);
    ``level`` passes 0 or 1; ``int`` and ``text`` pass their value; ``archive`` is the
    staged upload, passed as the positional argument. ``disabled_when`` lists
    ``(other key, value)`` pairs that make the option inapplicable.
    """

    key: str
    kind: OptionKind
    flag: str | None
    label: str
    help: str
    unit: str = ""
    minimum: int | None = None
    maximum: int | None = None
    placeholder: str = ""
    danger: bool = False
    disabled_when: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class JobSpec:
    key: str
    name: str
    description: str
    title: str
    cli: tuple[str, ...]
    scheduled: bool = False
    # The sync step that runs this job as part of every sync.
    sync_step: str | None = None
    options: tuple[JobOption, ...] = field(default_factory=tuple)


def _limit(unit: str, help_text: str = "Leave empty for all.") -> JobOption:
    return JobOption("limit", "int", "--limit", "Limit", help_text, unit=unit, minimum=1)


JOBS: tuple[JobSpec, ...] = (
    JobSpec(
        "sync",
        "Sync",
        "Bookmarks, likes and your posts",
        "tweetnook sync",
        ("sync",),
        scheduled=True,
    ),
    JobSpec(
        "check",
        "Availability check",
        "Are saved posts still on X?",
        "tweetnook check",
        ("check",),
        scheduled=True,
    ),
    JobSpec(
        "threads",
        "Expand conversations",
        "Replies and quotes for saved posts",
        "tweetnook threads expand",
        ("threads", "expand"),
        sync_step="threads",
        options=(
            JobOption(
                "refresh",
                "flag",
                "--refresh",
                "Re-fetch expanded threads",
                "Fetch threads again even if they were already expanded.",
            ),
            JobOption(
                "reply_linked_depth",
                "level",
                "--reply-linked-depth",
                "Include links in replies",
                "Also follow quotes and links in other people's replies, one hop.",
            ),
            JobOption(
                "max_linked_depth",
                "int",
                "--max-linked-depth",
                "Follow depth",
                "How far to follow quoted and linked posts. 0 turns this off.",
                unit="hops",
                minimum=0,
            ),
            _limit("threads", "Leave empty to expand every due thread."),
        ),
    ),
    JobSpec(
        "articles",
        "Refresh articles",
        "Article text and covers",
        "tweetnook articles refresh",
        ("articles", "refresh"),
        sync_step="articles",
        options=(
            JobOption(
                "all",
                "flag",
                "--all",
                "Refresh every article",
                "Not just articles saved as previews.",
            ),
            _limit("articles"),
        ),
    ),
    JobSpec(
        "media",
        "Download media",
        "Photos, GIFs and videos",
        "tweetnook media download",
        ("media", "download"),
        sync_step="media",
        options=(
            JobOption(
                "photos_only", "flag", "--photos-only", "Photos only", "Skip videos and GIFs."
            ),
            JobOption(
                "retry_failed",
                "flag",
                "--retry-failed",
                "Retry failed downloads",
                "Include files that failed before, not only new ones.",
            ),
            _limit("files"),
        ),
    ),
    JobSpec(
        "urls",
        "Link previews",
        "Titles and cards for saved links",
        "tweetnook unfurl",
        ("unfurl",),
        sync_step="urls",
        options=(
            JobOption(
                "retry_failed",
                "flag",
                "--retry-failed",
                "Retry failed links",
                "Include links that failed before, not only new ones.",
            ),
            _limit("links"),
        ),
    ),
    JobSpec(
        "tagging",
        "Automated tags",
        "Suggest tags for new posts",
        "tweetnook tag",
        ("tag",),
        sync_step="tagging",
        options=(
            JobOption(
                "target",
                "text",
                None,
                "Only this post",
                "Leave empty to tag every untagged post.",
                placeholder="Post URL or ID",
            ),
            JobOption(
                "test",
                "flag",
                "--test",
                "Preview only",
                "Show tags for one post without saving them.",
            ),
            _limit("requests", "Most Gemini requests this run."),
            JobOption(
                "batch",
                "int",
                "--batch",
                "Posts per request",
                "Free mode only. Leave empty to use the setting.",
                unit="posts",
                minimum=1,
            ),
            JobOption(
                "model",
                "text",
                "--model",
                "Model",
                "Leave empty to use the model from Settings.",
            ),
        ),
    ),
    JobSpec(
        "import",
        "Import X archive",
        "Load posts from a downloaded archive",
        "tweetnook import x-archive",
        ("import", "x-archive"),
        options=(
            JobOption(
                "archive",
                "archive",
                None,
                "Archive",
                "The .zip you downloaded from X.",
            ),
            JobOption(
                "enrich",
                "negflag",
                "--no-enrich",
                "Fill in details after import",
                "Look up posts the archive only has partly.",
                disabled_when=(("offline", True),),
            ),
            JobOption(
                "detail_lookups",
                "int",
                "--detail-lookups",
                "Detail lookups",
                "Most posts to fill in now. Leave empty for all.",
                unit="posts",
                minimum=1,
                disabled_when=(("offline", True), ("enrich", False)),
            ),
            JobOption(
                "offline",
                "flag",
                "--offline",
                "Offline import",
                "Load the archive and its media without contacting X.",
            ),
            JobOption(
                "regen",
                "flag",
                "--regen",
                "Replace the earlier import",
                "Removes rows and media from earlier archive imports first. Synced posts are kept.",
                danger=True,
            ),
        ),
    ),
    JobSpec(
        "enrich",
        "Enrich archive",
        "Fill in details for imported posts",
        "tweetnook import enrich",
        ("import", "enrich"),
        options=(_limit("posts", "Leave empty to fill in every post due now."),),
    ),
)
JOBS_BY_KEY = {job.key: job for job in JOBS}
JOBS_BY_TITLE = {job.title: job for job in JOBS}


class JobOptionsError(ValueError):
    """The run options don't fit the job's flags."""


def option_defaults(job: JobSpec, config: AppConfig) -> dict[str, Any]:
    """What each option is when left alone: the CLI's own defaults, from config."""

    defaults: dict[str, Any] = {}
    for option in job.options:
        if option.kind in {"flag", "level"}:
            defaults[option.key] = False
        elif option.kind == "negflag":
            defaults[option.key] = True
        else:
            defaults[option.key] = None
    if job.key == "threads":
        defaults["reply_linked_depth"] = bool(config.sync.reply_linked_depth)
        defaults["max_linked_depth"] = config.sync.max_linked_depth
    return defaults


def _option_value(option: JobOption, value: Any) -> Any:
    if option.kind in {"flag", "negflag", "level"}:
        if not isinstance(value, bool):
            raise JobOptionsError(f"{option.label} must be on or off.")
        return value
    if option.kind == "int":
        if value is None or value == "":
            return None
        if isinstance(value, bool) or not isinstance(value, int | str):
            raise JobOptionsError(f"{option.label} must be a whole number.")
        try:
            number = int(value)
        except ValueError as exc:
            raise JobOptionsError(f"{option.label} must be a whole number.") from exc
        if option.minimum is not None and number < option.minimum:
            raise JobOptionsError(f"{option.label} must be at least {option.minimum}.")
        if option.maximum is not None and number > option.maximum:
            raise JobOptionsError(f"{option.label} must be at most {option.maximum}.")
        return number
    if value is None:
        return None
    if not isinstance(value, str):
        raise JobOptionsError(f"{option.label} must be text.")
    text = value.strip()
    if text.startswith("-"):
        raise JobOptionsError(f"{option.label} can't start with a dash.")
    return text or None


def job_cli_args(
    job: JobSpec,
    options: dict[str, Any],
    config: AppConfig,
    *,
    staged_archive: str | None = None,
) -> list[str]:
    """The CLI arguments for one run: only options that differ from their default."""

    known = {option.key: option for option in job.options}
    unknown = sorted(set(options) - set(known))
    if unknown:
        raise JobOptionsError(f"Unknown option for {job.name}: {', '.join(unknown)}.")
    defaults = option_defaults(job, config)
    values = {
        **defaults,
        **{key: _option_value(known[key], value) for key, value in options.items()},
    }
    args = list(job.cli)
    positional: list[str] = []
    for option in job.options:
        value = values[option.key]
        if any(values.get(other) == wanted for other, wanted in option.disabled_when):
            continue
        if option.kind == "archive":
            if not staged_archive:
                raise JobOptionsError("Choose an archive to import.")
            positional.append(staged_archive)
            continue
        if value == defaults[option.key] or value is None:
            continue
        if option.kind == "flag" and value:
            args.append(str(option.flag))
        elif option.kind == "negflag" and not value:
            args.append(str(option.flag))
        elif option.kind == "level":
            args.extend((str(option.flag), "1" if value else "0"))
        elif option.flag is None:
            positional.append(str(value))
        else:
            args.extend((option.flag, str(value)))
    return args + positional


def _run_record(run: dict[str, Any]) -> dict[str, Any]:
    return {
        key: run.get(key)
        for key in (
            "run_id",
            "title",
            "origin",
            "status",
            "success",
            "started_at",
            "completed_at",
            "summary",
        )
    }


def jobs_payload(
    *,
    config: AppConfig,
    data_dir: Any,
    active: dict[str, Any] | None,
    queue: list[dict[str, Any]],
    schedule: dict[str, Any],
    check: dict[str, Any],
    sync_deferred: bool,
    setup_queue: list[str] | None = None,
) -> dict[str, Any]:
    """Every job with its state, last run and next run, for the Web jobs list.

    ``setup_queue`` holds jobs Setup starts on its own after the current one.
    """

    last: dict[str, dict[str, Any]] = {}
    runs = list_runs(data_dir, limit=200) if data_dir is not None else []
    week_ago = time.time() - 7 * 86400
    for run in runs:
        job = JOBS_BY_TITLE.get(str(run.get("title") or ""))
        if job is None or job.key in last or run.get("status") in {"starting", "running"}:
            continue
        last[job.key] = _run_record(run)
    running_key = JOBS_BY_TITLE.get(str((active or {}).get("title") or ""))
    running_key = running_key.key if running_key else None
    active_steps = {
        step.get("key"): step.get("state") for step in (active or {}).get("steps") or []
    }
    queued = {item["kind"]: index for index, item in enumerate(queue)}
    now = time.time()
    jobs = []
    for job in JOBS:
        record = last.get(job.key)
        state = "never" if record is None else "ok"
        if (
            record is not None
            and record.get("success") is False
            and record.get("status") != "stopped"
        ):
            state = "failed"
        queue_info = None
        if job.key in queued:
            state = "queued"
            queue_info = {"position": queued[job.key] + 1, "retry_at": None, "removable": True}
        elif job.key in (setup_queue or []):
            state = "queued"
            queue_info = {"position": None, "retry_at": None, "removable": False}
        if job.key == "check" and check.get("deferred"):
            state = "queued"
            queue_info = {"position": None, "retry_at": check.get("next_run_at"), "removable": True}
        if job.key == "sync" and sync_deferred and schedule.get("next_run_at"):
            state = "queued"
            queue_info = {
                "position": None,
                "retry_at": schedule.get("next_run_at"),
                "removable": False,
            }
        if running_key == job.key:
            state = "running"
        elif (
            running_key == "sync" and job.sync_step and active_steps.get(job.sync_step) == "active"
        ):
            state = "in_sync"
        if job.key == "sync":
            next_at = schedule.get("next_run_at") if schedule.get("enabled") else None
            next_info = {"at": next_at, "label": None if next_at else "Off"}
        elif job.key == "check":
            next_at = check.get("next_run_at")
            next_info = {"at": next_at, "label": None if next_at else "Off"}
        elif job.sync_step and (job.key != "tagging" or config.tagging.enabled):
            next_info = {"at": None, "label": "With sync"}
        else:
            next_info = {"at": None, "label": None}
        available, reason = True, None
        if job.key == "check" and not fx_enabled(config):
            available, reason = False, "Needs FxTwitter"
        snapshot = None
        if record is not None and data_dir is not None:
            try:
                snapshot = (read_run(data_dir, str(record["run_id"])) or {}).get("snapshot")
            except ValueError:
                snapshot = None
        jobs.append(
            {
                "key": job.key,
                "name": job.name,
                "description": job.description,
                "command": " ".join(("tweetnook", *job.cli)),
                "scheduled": job.scheduled,
                "state": state,
                "available": available,
                "unavailable_reason": reason,
                "last": record,
                "last_snapshot": snapshot,
                "next": next_info,
                "queue": queue_info,
                "options": [asdict(option) for option in job.options],
                "defaults": option_defaults(job, config),
            }
        )
    return {
        "jobs": jobs,
        "running": (
            {
                "key": running_key,
                "run_id": active.get("run_id"),
                "origin": active.get("origin"),
                "started_at": active.get("started_at"),
            }
            if active and running_key
            else None
        ),
        "runs_this_week": sum(1 for run in runs if (run.get("started_at") or 0) >= week_ago),
        "now": now,
    }
