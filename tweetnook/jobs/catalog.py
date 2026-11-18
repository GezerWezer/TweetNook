"""Every job the CLI can run, declared once for the CLI, the Web, the scheduler and Setup.

Each option maps to one CLI flag, so a run started from the Web takes exactly the
arguments the same command takes in a terminal. A run is titled with its command, so
run history finds a job's runs by that title.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from tweetnook.config import AppConfig
from tweetnook.jobs.steps import ARTICLES, MEDIA, TAGGING, THREADS, URLS


def command_title(cli: Sequence[str]) -> str:
    """A run's title: its command, which is how run history names it."""
    return " ".join(("tweetnook", *cli))


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
    # The CLI's own default when it comes from config rather than the flag's off state.
    config_default: Callable[[AppConfig], Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """The option as the Web jobs list describes it."""
        payload = asdict(self)
        payload.pop("config_default")
        return payload


@dataclass(frozen=True, slots=True)
class JobSpec:
    key: str
    name: str
    description: str
    cli: tuple[str, ...]
    scheduled: bool = False
    # The sync step that runs this job as part of every sync.
    sync_step: str | None = None
    options: tuple[JobOption, ...] = field(default_factory=tuple)

    @property
    def title(self) -> str:
        return command_title(self.cli)


def _limit(unit: str, help_text: str = "Leave empty for all.") -> JobOption:
    return JobOption("limit", "int", "--limit", "Limit", help_text, unit=unit, minimum=1)


JOBS: tuple[JobSpec, ...] = (
    JobSpec(
        "sync",
        "Sync",
        "Bookmarks, likes and your posts",
        ("sync",),
        scheduled=True,
    ),
    JobSpec(
        "check",
        "Availability check",
        "Are saved posts still on X?",
        ("check",),
        scheduled=True,
    ),
    JobSpec(
        "threads",
        "Expand conversations",
        "Replies and quotes for saved posts",
        ("threads", "expand"),
        sync_step=THREADS.key,
        options=(
            JobOption(
                "reply_linked_depth",
                "level",
                "--reply-linked-depth",
                "Include links in replies",
                "Also follow quotes and links in other people's replies, one hop.",
                config_default=lambda config: bool(config.sync.reply_linked_depth),
            ),
            JobOption(
                "max_linked_depth",
                "int",
                "--max-linked-depth",
                "Follow depth",
                "How far to follow quoted and linked posts. 0 turns this off.",
                unit="hops",
                minimum=0,
                config_default=lambda config: config.sync.max_linked_depth,
            ),
            _limit("threads", "Leave empty to expand every due thread."),
        ),
    ),
    JobSpec(
        "articles",
        "Refresh articles",
        "Article text and covers",
        ("articles", "refresh"),
        sync_step=ARTICLES.key,
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
        ("media", "download"),
        sync_step=MEDIA.key,
        options=(
            JobOption(
                "photos_only", "flag", "--photos-only", "Photos only", "Skip videos and GIFs."
            ),
            JobOption(
                "retry_failed",
                "flag",
                "--retry-failed",
                "Retry failed downloads",
                "Include media items that failed before.",
            ),
            _limit(
                "items",
                "Leave empty for all media items. Videos and GIFs can include poster files.",
            ),
        ),
    ),
    JobSpec(
        "urls",
        "Link previews",
        "Titles and cards for saved links",
        ("unfurl",),
        sync_step=URLS.key,
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
        ("tag",),
        sync_step=TAGGING.key,
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
        "smart-search",
        "Smart search index",
        "Index archived images for semantic search",
        ("smart-search", "index"),
        options=(_limit("images"),),
    ),
    JobSpec(
        "import",
        "Import X archive",
        "Load posts from a downloaded archive",
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
        if option.config_default is not None:
            defaults[option.key] = option.config_default(config)
        elif option.kind in {"flag", "level"}:
            defaults[option.key] = False
        elif option.kind == "negflag":
            defaults[option.key] = True
        else:
            defaults[option.key] = None
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
