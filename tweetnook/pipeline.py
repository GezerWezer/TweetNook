"""Shared progress reporting for interactive commands and unattended jobs."""

from __future__ import annotations

import json
import os
import random
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any, Literal

from rich import box
from rich.cells import cell_len
from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

TWITTER_BLUE = "#1DA1F2"
SPINNER_INTERVAL_MS = 80
SPINNER_FRAMES: dict[str, tuple[str, ...]] = {
    "wave": (
        "⠁⠂⠄⡀",
        "⠂⠄⡀⢀",
        "⠄⡀⢀⠠",
        "⡀⢀⠠⠐",
        "⢀⠠⠐⠈",
        "⠠⠐⠈⠁",
        "⠐⠈⠁⠂",
        "⠈⠁⠂⠄",
    ),
    "wave2": ("⡀⠀⠀", "⠄⡀⠀", "⠂⠄⡀", "⠁⠂⠄", "⠈⠁⠂", "⠐⠈⠁", "⠠⠐⠈", "⡀⠠⠐", "⠄⡀⠠", "⠂⠄⡀"),
    "bounce": ("⠄", "⠆", "⠇", "⠋", "⠙", "⠸", "⠰", "⠠", "⠰", "⠸", "⠙", "⠋", "⠇", "⠆"),
    "scan": ("⠀⠀⠀⠀", "⡇⠀⠀⠀", "⣿⠀⠀⠀", "⢸⡇⠀⠀", "⠀⣿⠀⠀", "⠀⢸⡇⠀", "⠀⠀⣿⠀", "⠀⠀⢸⡇", "⠀⠀⠀⣿", "⠀⠀⠀⢸"),
    "sand": tuple(
        "⠁.⠂.⠄.⡀.⡈.⡐.⡠.⣀.⣁.⣂.⣄.⣌.⣔.⣤.⣥.⣦.⣮.⣶.⣷.⣿.⡿.⠿.⢟.⠟.⡛.⠛.⠫.⢋.⠋.⠍.⡉.⠉.⠑.⠡.⢁".split(".")
    ),
    "rain": (
        "⢁⠂⠔⠈",
        "⠂⠌⡠⠐",
        "⠄⡐⢀⠡",
        "⡈⠠⠀⢂",
        "⠐⢀⠁⠄",
        "⠠⠁⠊⡀",
        "⢁⠂⠔⠈",
        "⠂⠌⡠⠐",
        "⠄⡐⢀⠡",
        "⡈⠠⠀⢂",
        "⠐⢀⠁⠄",
        "⠠⠁⠊⡀",
    ),
    "breath": ("⠀", "⠂", "⠌", "⡑", "⢕", "⢝", "⣫", "⣟", "⣿", "⣟", "⣫", "⢝", "⢕", "⡑", "⠌", "⠂", "⠀"),
    "cascade": (
        "⠀⠀⠀⠀",
        "⠀⠀⠀⠀",
        "⠁⠀⠀⠀",
        "⠋⠀⠀⠀",
        "⠞⠁⠀⠀",
        "⡴⠋⠀⠀",
        "⣠⠞⠁⠀",
        "⢀⡴⠋⠀",
        "⠀⣠⠞⠁",
        "⠀⢀⡴⠋",
        "⠀⠀⣠⠞",
        "⠀⠀⢀⡴",
        "⠀⠀⠀⣠",
        "⠀⠀⠀⢀",
    ),
    "flip": ("_", "_", "_", "-", "`", "`", "'", "´", "-", "_", "_", "_"),  # noqa: RUF001
    "spin": tuple(
        "⢀⠀.⡀⠀.⠄⠀.⢂⠀.⡂⠀.⠅⠀.⢃⠀.⡃⠀.⠍⠀.⢋⠀.⡋⠀.⠍⠁.⢋⠁.⡋⠁.⠍⠉.⠋⠉.⠋⠉.⠉⠙.⠉⠙.⠉⠩.⠈⢙.⠈⡙.⢈⠩.⡀⢙.⠄⡙.⢂⠩.⡂⢘.⠅⡘.⢃⠨.⡃⢐.⠍⡐.⢋⠠.⡋⢀.⠍⡁.⢋⠁.⡋⠁.⠍⠉.⠋⠉.⠋⠉.⠉⠙.⠉⠙.⠉⠩.⠈⢙.⠈⡙.⠈⠩.⠀⢙.⠀⡙.⠀⠩.⠀⢘.⠀⡘.⠀⠨.⠀⢐.⠀⡐.⠀⠠.⠀⢀.⠀⡀".split(
            "."
        )
    ),
    "gradient": (
        "░░░░░░",
        "▓░░░░░",
        "▒▓░░░░",
        "░▒▓░░░",
        "░░▒▓░░",
        "░░░▒▓░",
        "░░░░▒▓",
        "░░░░░▒",
        "░░░░░░",
    ),
    "progressDots": (
        "⠀⠀⠀⠀",
        "⣀⠀⠀⠀",
        "⣿⠀⠀⠀",
        "⣿⣀⠀⠀",
        "⣿⣿⠀⠀",
        "⣿⣿⣀⠀",
        "⣿⣿⣿⠀",
        "⣿⣿⣿⣀",
        "⣿⣿⣿⣿",
        "⠛⣿⣿⣿",
        "⠀⣿⣿⣿",
        "⠀⠛⣿⣿",
        "⠀⠀⣿⣿",
        "⠀⠀⠛⣿",
        "⠀⠀⠀⣿",
        "⠀⠀⠀⠛",
        "⠀⠀⠀⠀",
    ),
}

StepState = Literal["pending", "active", "complete", "skipped", "failed"]
IssueLevel = Literal["warning", "error"]
_SERVICE_ENV_VARS = ("INVOCATION_ID", "JOURNAL_STREAM")
_CURRENT_PIPELINE: ContextVar[PipelineReporter | None] = ContextVar(
    "tweetnook_pipeline", default=None
)


def current_pipeline() -> PipelineReporter | None:
    """Return the reporter active for this command lifecycle, if any."""

    return _CURRENT_PIPELINE.get()


def _clean_line(value: str) -> str:
    return " ".join(value.split())


def _format_duration(seconds: float) -> str:
    whole = max(round(seconds), 0)
    hours, remainder = divmod(whole, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


@dataclass(slots=True)
class PipelineIssue:
    level: IssueLevel
    message: str
    count: int = 1
    latest: str | None = None


@dataclass(slots=True)
class PipelineStep:
    key: str
    title: str
    total: int
    unit: str
    detail: str = ""
    rate_unit: str | None = None
    show_rate: bool = True
    show_eta: bool = True
    state: StepState = "pending"
    completed: int = 0
    activity: str = ""
    counters: str = ""
    summary: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    last_log_at: float = 0.0
    last_log_bucket: int = -1

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        return max((self.finished_at or time.monotonic()) - self.started_at, 0.0)

    @property
    def rate(self) -> float | None:
        if not self.show_rate or self.completed <= 0 or self.elapsed <= 0:
            return None
        return self.completed / self.elapsed

    @property
    def eta(self) -> float | None:
        rate = self.rate
        if not self.show_eta or rate is None or self.completed >= self.total:
            return None
        return max(self.total - self.completed, 0) / rate


class _FrameSpinner:
    def __init__(self, name: str) -> None:
        self.name = name

    def __rich__(self) -> Text:
        frames = SPINNER_FRAMES[self.name]
        index = int(time.monotonic() * 1000 / SPINNER_INTERVAL_MS) % len(frames)
        return Text(frames[index], style=TWITTER_BLUE)


class _ResponsivePipeline:
    def __init__(self, pipeline: RenderableType, issues: RenderableType) -> None:
        self.pipeline = pipeline
        self.issues = issues

    def __rich_console__(self, _console: Console, options: ConsoleOptions) -> RenderResult:
        if options.max_width >= 104:
            sidebar_width = min(42, max(34, options.max_width // 3))
            layout = Table.grid(expand=True, padding=(0, 2))
            layout.add_column(ratio=1)
            layout.add_column(width=sidebar_width)
            layout.add_row(self.pipeline, self.issues)
            yield layout
            return
        yield self.pipeline
        yield Text()
        yield self.issues


class _CapturedPipelineConsole:
    """Small Console-compatible sink for legacy workers inside a live pipeline."""

    def __init__(self, reporter: PipelineReporter, step_key: str) -> None:
        self.reporter = reporter
        self.step_key = step_key
        self.file = reporter.console.file
        self.is_terminal = False

    def print(self, *objects, **kwargs) -> None:
        buffer = StringIO()
        capture = Console(file=buffer, force_terminal=False, color_system=None, width=160)
        capture.print(*objects, **kwargs)
        message = _clean_line(buffer.getvalue())
        if not message:
            return
        style_tokens: list[str] = []
        for item in objects:
            style = getattr(item, "style", None)
            if style:
                style_tokens.append(str(style))
            for span in getattr(item, "spans", ()):
                style_tokens.append(str(span.style))
        raw = " ".join(map(str, objects)).lower()
        styles = " ".join(style_tokens).lower()
        if "[red]" in raw or "red" in styles or "error" in raw or "failed" in raw:
            self.reporter.issue(
                message,
                level="error",
                dedupe_key=f"{self.step_key}:captured-error",
            )
        elif "[yellow]" in raw or "yellow" in styles or "warning" in raw or "retry" in raw:
            self.reporter.issue(
                message,
                dedupe_key=f"{self.step_key}:captured-warning",
            )
        elif self.step_key in self.reporter._step_by_key:
            self.reporter.status(self.step_key, message, important=True)


@dataclass(slots=True)
class PipelineReporter:
    """Render one live pipeline or emit bounded plain records for a whole command."""

    console: Console
    title: str
    spinner_name: str | None = None
    interactive: bool | None = None
    log_interval_seconds: float = 30.0
    state_path: Path | None = None
    steps: list[PipelineStep] = field(default_factory=list, init=False)
    issues: list[PipelineIssue] = field(default_factory=list, init=False)
    _step_by_key: dict[str, PipelineStep] = field(default_factory=dict, init=False)
    _issue_by_key: dict[str, PipelineIssue] = field(default_factory=dict, init=False)
    _live: Live | None = field(default=None, init=False)
    _token: Token[PipelineReporter | None] | None = field(default=None, init=False)
    _finished: bool = field(default=False, init=False)
    _success: bool = field(default=True, init=False)
    _final_summary: str = field(default="", init=False)
    _started_at: float | None = field(default=None, init=False)
    _finished_at: float | None = field(default=None, init=False)
    _started_at_unix: float | None = field(default=None, init=False)
    _finished_at_unix: float | None = field(default=None, init=False)
    _run_writer: Any | None = field(default=None, init=False)
    _command_lock: Any | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.spinner_name is None:
            self.spinner_name = random.choice(tuple(SPINNER_FRAMES))
        if self.spinner_name not in SPINNER_FRAMES:
            raise ValueError(f"Unknown spinner '{self.spinner_name}'.")
        service_environment = any(os.environ.get(name) for name in _SERVICE_ENV_VARS)
        if self.interactive is None:
            self.interactive = self.console.is_terminal and not service_environment

    @property
    def run_id(self) -> str | None:
        """Return the durable activity-run identifier when history is active."""
        return str(self._run_writer.run_id) if self._run_writer is not None else None

    def __enter__(self) -> PipelineReporter:
        self._started_at = time.monotonic()
        self._started_at_unix = time.time()
        if self.state_path is not None:
            from tweetnook.locking import ProcessLock

            self._command_lock = ProcessLock(
                self.state_path.with_name("command.lock"),
                conflict_message=(
                    "Another tweetnook command is already running. Wait for it to finish "
                    "before starting sync, import, or enrichment work."
                ),
            )
            self._command_lock.acquire()
            try:
                from tweetnook.activity_history import ActivityRunWriter

                self._run_writer = ActivityRunWriter(self.state_path, self.title)
                self._run_writer.start(pid=os.getpid(), started_at=self._started_at_unix)
            except (OSError, ValueError):
                self._run_writer = None
        self._token = _CURRENT_PIPELINE.set(self)
        if self.interactive:
            self._record_history("command", "start")
            self._live = Live(
                self,
                console=self.console,
                refresh_per_second=20,
                transient=False,
            )
            self._live.start(refresh=True)
        else:
            self._log("command", "start")
        return self

    def __exit__(self, exc_type, exc, _traceback) -> None:
        if exc is not None:
            active = self.active_step
            message = (
                "Stopped by user"
                if exc_type is KeyboardInterrupt
                else (_clean_line(str(exc)) or exc.__class__.__name__)
            )
            if active is not None and active.state == "active":
                self.fail_step(active.key, message)
            self.issue(message, level="error", dedupe_key="command:failure")
            self.finish(message, success=False)
        elif not self._finished:
            self.finish(success=True)
        if self._live is not None:
            self._live.stop()
            self._live = None
        if self._token is not None:
            _CURRENT_PIPELINE.reset(self._token)
            self._token = None
        if self._command_lock is not None:
            self._command_lock.release()
            self._command_lock = None

    @property
    def active_step(self) -> PipelineStep | None:
        return next((step for step in self.steps if step.state == "active"), None)

    @property
    def has_final_note(self) -> bool:
        return bool(self._final_summary)

    @property
    def elapsed(self) -> float:
        if self._started_at is None:
            return 0.0
        return max((self._finished_at or time.monotonic()) - self._started_at, 0.0)

    def add_step(
        self,
        key: str,
        title: str,
        *,
        total: int,
        unit: str,
        detail: str = "",
        rate_unit: str | None = None,
        show_rate: bool = True,
        show_eta: bool = True,
    ) -> PipelineStep:
        if key in self._step_by_key:
            step = self._step_by_key[key]
            step.total = max(total, 1)
            step.unit = unit
            step.detail = detail or step.detail
            return step
        step = PipelineStep(
            key=key,
            title=title,
            total=max(total, 1),
            unit=unit,
            detail=detail,
            rate_unit=rate_unit,
            show_rate=show_rate,
            show_eta=show_eta,
        )
        self.steps.append(step)
        self._step_by_key[key] = step
        self._refresh()
        return step

    def has_step(self, key: str) -> bool:
        return key in self._step_by_key

    def step_state(self, key: str) -> StepState | None:
        step = self._step_by_key.get(key)
        return step.state if step is not None else None

    def capture_console(self, step_key: str) -> _CapturedPipelineConsole:
        return _CapturedPipelineConsole(self, step_key)

    def start_step(
        self,
        key: str,
        *,
        activity: str,
        counters: str = "",
        detail: str | None = None,
    ) -> None:
        step = self._step_by_key[key]
        active = self.active_step
        if active is not None and active is not step and active.state == "active":
            raise RuntimeError(f"Pipeline step '{active.key}' is still active.")
        step.state = "active"
        step.activity = activity
        step.counters = counters
        if detail is not None:
            step.detail = detail
        if step.started_at is None:
            step.started_at = time.monotonic()
        fields = [activity]
        if step.total > 0:
            fields.append(f"total={step.total:,} {step.unit}")
        if step.detail:
            fields.append(step.detail)
        if self.interactive:
            self._record_history(step.title, "start", " · ".join(fields))
        else:
            self._log(step.title, "start", " · ".join(fields))
        self._refresh()

    def update_step(
        self,
        key: str,
        *,
        completed: int | None = None,
        total: int | None = None,
        activity: str | None = None,
        counters: str | None = None,
        detail: str | None = None,
        important: bool = False,
    ) -> None:
        step = self._step_by_key[key]
        if step.state == "pending":
            self.start_step(
                key,
                activity=activity or step.activity or f"Processing {step.title.lower()}",
                counters=counters or "",
                detail=detail,
            )
        if total is not None:
            step.total = max(total, 1)
        if completed is not None:
            step.completed = max(min(completed, step.total), 0)
        if activity is not None:
            step.activity = activity
        if counters is not None:
            step.counters = counters
        if detail is not None:
            step.detail = detail
        if self._should_log_progress(step, important=important):
            progress = f"{step.completed:,}/{step.total:,} {step.unit}"
            fields = [progress]
            if step.counters:
                fields.append(step.counters)
            elif step.activity:
                fields.append(step.activity)
            if self.interactive:
                self._record_history(step.title, "progress", " · ".join(fields))
            else:
                self._log(step.title, "progress", " · ".join(fields))
        self._refresh()

    def status(self, key: str, message: str, *, important: bool = False) -> None:
        step = self._step_by_key[key]
        step.activity = message
        if important:
            if self.interactive:
                self._record_history(step.title, "status", message)
            else:
                self._log(step.title, "status", message)
        self._refresh()

    def detail(self, scope: str, message: str) -> None:
        """Emit an explicit diagnostic without placing it in the issues panel."""

        if self.interactive and self._live is not None:
            self._record_history(scope, "detail", message)
            self._live.console.print(
                " | ".join((self.title, scope, "detail", _clean_line(message))),
                markup=False,
                highlight=False,
                soft_wrap=True,
            )
        else:
            self._log(scope, "detail", message)

    def issue(
        self,
        message: str,
        *,
        level: IssueLevel = "warning",
        dedupe_key: str | None = None,
    ) -> None:
        message = _clean_line(message)
        key = dedupe_key or f"{level}:{message}"
        issue = self._issue_by_key.get(key)
        if issue is None:
            issue = PipelineIssue(level=level, message=message)
            self._issue_by_key[key] = issue
            self.issues.append(issue)
        else:
            issue.count += 1
            issue.latest = message
        if issue.count <= 3 or issue.count == 10 or issue.count % 25 == 0:
            suffix = "" if issue.count == 1 else f" · occurrences={issue.count}"
            if self.interactive:
                self._record_history("issue", level, message + suffix)
            else:
                self._log("issue", level, message + suffix)
        self._refresh()

    def complete_step(
        self,
        key: str,
        summary: str,
        *,
        counters: str | None = None,
    ) -> None:
        step = self._step_by_key[key]
        step.state = "complete"
        step.completed = step.total
        step.summary = summary
        if counters is not None:
            step.counters = counters
        step.finished_at = time.monotonic()
        fields = [summary, f"elapsed {_format_duration(step.elapsed)}"]
        if self.interactive:
            self._record_history(step.title, "complete", " · ".join(fields))
        else:
            self._log(step.title, "complete", " · ".join(fields))
        self._refresh()

    def skip_step(self, key: str, reason: str) -> None:
        """Resolve a planned step that has no work without treating it as success work."""

        step = self._step_by_key[key]
        if step.state in {"complete", "skipped", "failed"}:
            return
        step.state = "skipped"
        step.completed = 0
        clean_reason = _clean_line(reason).rstrip(".")
        step.summary = f"skipped due to {clean_reason}"
        if step.started_at is not None:
            step.finished_at = time.monotonic()
        if self.interactive:
            self._record_history(step.title, "skipped", step.summary)
        else:
            self._log(step.title, "skipped", step.summary)
        self._refresh()

    def fail_step(self, key: str, message: str) -> None:
        step = self._step_by_key[key]
        step.state = "failed"
        step.summary = message
        step.finished_at = time.monotonic()
        if self.interactive:
            self._record_history(step.title, "failed", message)
        else:
            self._log(step.title, "failed", message)
        self._refresh()

    def remove_step(self, key: str) -> None:
        step = self._step_by_key.get(key)
        if step is None:
            return
        if step.state != "pending":
            raise RuntimeError("Only pending pipeline steps can be removed.")
        self.steps.remove(step)
        del self._step_by_key[key]
        self._refresh()

    def finish(self, summary: str = "", *, success: bool = True) -> None:
        if self._finished:
            return
        self._finished = True
        self._finished_at = time.monotonic()
        self._finished_at_unix = time.time()
        self._success = success
        if summary:
            self._final_summary = summary
        pending_reason = (
            "an earlier failure stopped the command"
            if not success
            else "no work being found for this run"
        )
        for step in self.steps:
            if step.state == "pending":
                self.skip_step(step.key, pending_reason)
        if self.interactive:
            self._record_history(
                "command",
                "complete" if success else "failed",
                self._final_summary,
            )
        else:
            self._log(
                "command",
                "complete" if success else "failed",
                self._final_summary,
            )
        self._refresh()

    def final_note(self, summary: str) -> None:
        """Set a useful command-level note without ending the lifecycle."""

        self._final_summary = _clean_line(summary)
        self._refresh()

    def _should_log_progress(self, step: PipelineStep, *, important: bool) -> bool:
        now = time.monotonic()
        if important:
            step.last_log_at = now
            return True
        percent = step.completed / step.total if step.total else 1.0
        bucket = min(int(percent * 10), 10)
        should_log = (
            step.last_log_bucket < 0
            or bucket > step.last_log_bucket
            or now - step.last_log_at >= self.log_interval_seconds
        )
        if should_log:
            step.last_log_bucket = bucket
            step.last_log_at = now
        return should_log

    def _format_log_line(self, scope: str, event: str, message: str = "") -> tuple[str, str]:
        clean_message = _clean_line(message)
        if scope == "command":
            if event == "start":
                line = f"{self.title}: started"
            else:
                line = f"{self.title}: {event} · elapsed {_format_duration(self.elapsed)}"
                if clean_message:
                    line += f" · {clean_message}"
        elif scope == "issue":
            line = f"{event.upper()}: {clean_message}"
        else:
            event_label = {
                "start": "starting",
                "complete": "complete",
                "failed": "failed",
                "skipped": "skipped",
                "progress": "progress",
                "status": "status",
                "detail": "detail",
            }.get(event, event)
            if event == "skipped" and clean_message.startswith("skipped "):
                line = f"{scope}: {clean_message}"
            else:
                line = f"{scope}: {event_label}"
            if clean_message and not (event == "skipped" and clean_message.startswith("skipped ")):
                line += f" · {clean_message}"
        return clean_message, line

    def _record_history(self, scope: str, event: str, message: str = "") -> str:
        clean_message, line = self._format_log_line(scope, event, message)
        if self._run_writer is not None:
            self._run_writer.append(
                scope=scope,
                event=event,
                message=clean_message,
                line=line,
            )
        return line

    def _log(self, scope: str, event: str, message: str = "") -> None:
        line = self._record_history(scope, event, message)
        self.console.print(line, markup=False, highlight=False, soft_wrap=True)

    def _refresh(self) -> None:
        snapshot = self._state_snapshot()
        self._write_state_snapshot(snapshot)
        if self._run_writer is not None:
            self._run_writer.snapshot(snapshot)
        if self._live is not None:
            self._live.update(self, refresh=True)

    def _state_snapshot(self) -> dict[str, object]:
        active = self.active_step
        return {
            "version": 1,
            "run_id": self._run_writer.run_id if self._run_writer is not None else None,
            "origin": self._run_writer.origin if self._run_writer is not None else "cli",
            "pid": os.getpid(),
            "title": self.title,
            "running": self._started_at is not None and not self._finished,
            "success": self._success if self._finished else None,
            "summary": self._final_summary,
            "started_at": self._started_at_unix,
            "completed_at": self._finished_at_unix,
            "updated_at": time.time(),
            "elapsed_seconds": self.elapsed,
            "active_step": active.key if active is not None else None,
            "steps": [
                {
                    "key": step.key,
                    "title": step.title,
                    "state": step.state,
                    "completed": step.completed,
                    "total": step.total,
                    "unit": step.unit,
                    "activity": step.activity,
                    "counters": step.counters,
                    "detail": step.detail,
                    "summary": step.summary,
                    "elapsed_seconds": step.elapsed,
                    "rate": step.rate,
                    "rate_unit": step.rate_unit or f"{step.unit}/s",
                    "eta_seconds": step.eta,
                }
                for step in self.steps
            ],
            "issues": [
                {
                    "level": issue.level,
                    "message": issue.message,
                    "count": issue.count,
                    "latest": issue.latest,
                }
                for issue in self.issues
            ],
        }

    def _write_state_snapshot(self, snapshot: dict[str, object] | None = None) -> None:
        if self.state_path is None or self._started_at is None:
            return
        temporary = self.state_path.with_name(f".{self.state_path.name}.{os.getpid()}.tmp")
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(snapshot or self._state_snapshot(), separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self.state_path)
        except OSError:
            # Status publishing must never break the underlying archive command.
            temporary.unlink(missing_ok=True)

    def _progress_renderable(self, step: PipelineStep) -> RenderableType:
        progress = Table.grid(expand=True, padding=(0, 1))
        progress.add_column(ratio=1)
        progress.add_column(justify="right", no_wrap=True)
        percent = step.completed / step.total * 100 if step.total else 100.0
        progress.add_row(
            ProgressBar(
                total=max(step.total, 1),
                completed=min(step.completed, step.total),
                style="grey23",
                complete_style=TWITTER_BLUE,
                finished_style=TWITTER_BLUE,
            ),
            f"{step.completed:,}/{step.total:,} {step.unit} · {percent:.1f}%",
        )

        detail = Table.grid(expand=True, padding=(0, 1))
        detail.add_column(ratio=1, overflow="fold")
        detail.add_column(justify="right", no_wrap=True)
        rate_parts: list[str] = []
        rate = step.rate
        if rate is not None:
            rate_parts.append(f"{rate:.2f} {step.rate_unit or f'{step.unit}/s'}")
        eta = step.eta
        if eta is not None:
            rate_parts.append(f"ETA ~{_format_duration(eta)}")
        detail.add_row(Text(step.detail, style="dim"), Text(" · ".join(rate_parts), style="dim"))
        return Group(progress, detail)

    def _issues_panel(self) -> Panel:
        if not self.issues:
            body: RenderableType = Text("No issues.", style="dim")
            border_style = "grey42"
        else:
            rows = Table.grid(expand=True, padding=(0, 1))
            rows.add_column(width=1, no_wrap=True)
            rows.add_column(ratio=1, overflow="fold")
            for issue in self.issues[-4:]:
                color = "red" if issue.level == "error" else "yellow"
                message = issue.message
                if issue.count > 1:
                    message += f" ({issue.count} occurrences"
                    if issue.latest and issue.latest != issue.message:
                        message += f"; latest: {issue.latest}"
                    message += ")"
                rows.add_row(Text("!", style=color), Text(message, style=color))
            hidden = len(self.issues) - 4
            if hidden > 0:
                rows.add_row("", Text(f"+{hidden} earlier issue groups", style="dim"))
            body = rows
            border_style = "yellow"
        issue_count = sum(issue.count for issue in self.issues)
        label = "issue" if issue_count == 1 else "issues"
        return Panel(
            body,
            title="[bold]Issues[/bold]",
            subtitle=f"[dim]{issue_count} {label}[/dim]",
            border_style=border_style,
            box=box.ROUNDED,
            padding=(0, 1),
        )

    def __rich__(self) -> RenderableType:
        header = Table.grid(expand=True)
        header.add_column(ratio=1)
        header.add_column(justify="right", no_wrap=True)
        active = self.active_step
        if self._finished:
            status = "complete" if self._success else "failed"
            status_style = "green" if self._success else "red"
        elif active is not None:
            active_index = self.steps.index(active) + 1
            status = f"step {active_index}/{len(self.steps)}"
            status_style = "dim"
        else:
            status = "preparing"
            status_style = "dim"
        status = f"{status} · elapsed {_format_duration(self.elapsed)}"
        header.add_row(Text(self.title, style="bold"), Text(status, style=status_style))

        steps = Table.grid(expand=True, padding=(0, 1))
        spinner_width = max(
            cell_len(frame) for frame in SPINNER_FRAMES[self.spinner_name or "wave"]
        )
        steps.add_column(width=spinner_width, no_wrap=True)
        steps.add_column(width=15, no_wrap=True)
        steps.add_column(ratio=1, overflow="ellipsis")
        steps.add_column(justify="right", no_wrap=True)
        for step in self.steps:
            if step.state == "complete":
                steps.add_row(
                    Text("✓", style="green"),
                    step.title,
                    Text(step.summary, style="dim"),
                    Text(_format_duration(step.elapsed), style="dim"),
                )
            elif step.state == "skipped":
                steps.add_row(
                    Text("─", style="grey50"),
                    Text(step.title, style="grey50"),
                    Text(step.summary, style="grey50"),
                    "",
                )
            elif step.state == "failed":
                elapsed = _format_duration(step.elapsed) if step.started_at is not None else ""
                steps.add_row(
                    Text("!", style="red"),
                    step.title,
                    Text(step.summary, style="red"),
                    Text(elapsed, style="red"),
                )
            elif step.state == "active":
                steps.add_row(
                    _FrameSpinner(self.spinner_name or "wave"),
                    Text(step.title, style=f"bold {TWITTER_BLUE}"),
                    Text(step.activity),
                    "",
                )
                if step.counters:
                    steps.add_row("", "", Text(step.counters, style="dim"), "")
                steps.add_row("", "", self._progress_renderable(step), "")
            else:
                steps.add_row(Text("·", style="grey50"), Text(step.title, style="grey50"), "", "")

        parts: list[RenderableType] = [header, Text(), steps]
        if self._finished and self._final_summary:
            style = "green" if self._success else "red"
            parts.extend((Text(), Text(self._final_summary, style=style)))
        return _ResponsivePipeline(Group(*parts), self._issues_panel())
