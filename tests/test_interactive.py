from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from tweetnook.interactive import emit_status, progress_callback, status_printer
from tweetnook.pipeline import PipelineReporter


class FakeConsole:
    def __init__(self, *, is_terminal: bool):
        self.is_terminal = is_terminal
        self.file = object()
        self.print_calls: list[tuple[tuple, dict]] = []

    def print(self, *args, **kwargs):
        self.print_calls.append((args, kwargs))


class FakeProgressBar:
    def __init__(self, kwargs: dict):
        self.kwargs = kwargs
        self.updates: list[int] = []
        self.exit_args: tuple | None = None

    def __enter__(self):
        return self

    def update(self, delta: int):
        self.updates.append(delta)

    def __exit__(self, *args):
        self.exit_args = args


def _install_fake_tqdm(monkeypatch) -> list[FakeProgressBar]:
    bars: list[FakeProgressBar] = []

    def make_bar(**kwargs):
        bar = FakeProgressBar(kwargs)
        bars.append(bar)
        return bar

    monkeypatch.setitem(sys.modules, "tqdm", SimpleNamespace(tqdm=make_bar))
    return bars


def test_emit_status_is_a_noop_without_callback() -> None:
    emit_status(None, "ignored")


def test_emit_status_forwards_message_exactly() -> None:
    messages: list[str] = []

    emit_status(messages.append, "phase complete")

    assert messages == ["phase complete"]


def test_status_printer_is_silent_for_non_tty() -> None:
    console = FakeConsole(is_terminal=False)

    status = status_printer(console, "media")

    assert status is None
    assert console.print_calls == []


@pytest.mark.parametrize(
    ("is_terminal", "force"),
    [(True, False), (True, True), (False, True)],
)
def test_status_printer_formats_tty_and_forced_messages(is_terminal: bool, force: bool) -> None:
    console = FakeConsole(is_terminal=is_terminal)

    status = status_printer(console, "media", force=force)
    assert status is not None
    status("downloading 3 files")

    assert console.print_calls == [(("media: downloading 3 files",), {"highlight": False})]


@pytest.mark.parametrize("total", [0, -1])
def test_progress_callback_is_disabled_for_nonpositive_totals(monkeypatch, total: int) -> None:
    console = FakeConsole(is_terminal=True)
    monkeypatch.setitem(
        sys.modules,
        "tqdm",
        SimpleNamespace(tqdm=lambda **_kwargs: pytest.fail("tqdm should not be constructed")),
    )

    with progress_callback(
        console,
        label="downloading",
        total=total,
        unit="files",
    ) as callback:
        assert callback is None


def test_progress_callback_is_disabled_for_non_tty(monkeypatch) -> None:
    console = FakeConsole(is_terminal=False)
    monkeypatch.setitem(
        sys.modules,
        "tqdm",
        SimpleNamespace(tqdm=lambda **_kwargs: pytest.fail("tqdm should not be constructed")),
    )

    with progress_callback(
        console,
        label="downloading",
        total=10,
        unit="files",
    ) as callback:
        assert callback is None


def test_progress_callback_is_disabled_inside_shared_pipeline(monkeypatch) -> None:
    console = FakeConsole(is_terminal=True)
    monkeypatch.setitem(
        sys.modules,
        "tqdm",
        SimpleNamespace(
            tqdm=lambda **_kwargs: pytest.fail("nested tqdm should not be constructed")
        ),
    )
    reporter = PipelineReporter(console, "sync", interactive=False)

    with reporter:
        with progress_callback(
            console,
            label="downloading",
            total=10,
            unit="files",
        ) as callback:
            assert callback is None


def test_progress_callback_configures_bar_and_updates_only_forward_deltas(
    monkeypatch,
) -> None:
    console = FakeConsole(is_terminal=True)
    bars = _install_fake_tqdm(monkeypatch)

    with progress_callback(
        console,
        label="downloading",
        total=10,
        unit="files",
        leave=True,
    ) as callback:
        assert callback is not None
        callback(3, 10)
        callback(3, 10)
        callback(2, 10)
        callback(5, 10)

    assert len(bars) == 1
    assert bars[0].kwargs == {
        "total": 10,
        "desc": "downloading",
        "unit": "files",
        "dynamic_ncols": True,
        "leave": True,
        "file": console.file,
    }
    assert bars[0].updates == [3, 2]
    assert bars[0].exit_args == (None, None, None)


def test_progress_callback_enables_binary_byte_scaling(monkeypatch) -> None:
    console = FakeConsole(is_terminal=True)
    bars = _install_fake_tqdm(monkeypatch)

    with progress_callback(
        console,
        label="media bytes",
        total=4096,
        unit="B",
    ) as callback:
        assert callback is not None
        callback(1024, 4096)

    assert bars[0].kwargs["unit_scale"] is True
    assert bars[0].kwargs["unit_divisor"] == 1024
    assert bars[0].kwargs["leave"] is False
    assert bars[0].updates == [1024]


def test_progress_callback_closes_bar_and_propagates_body_exception(
    monkeypatch,
) -> None:
    console = FakeConsole(is_terminal=True)
    bars = _install_fake_tqdm(monkeypatch)

    with pytest.raises(RuntimeError, match="download failed"):
        with progress_callback(
            console,
            label="downloading",
            total=1,
            unit="files",
        ):
            raise RuntimeError("download failed")

    error_type, error, traceback = bars[0].exit_args
    assert error_type is RuntimeError
    assert str(error) == "download failed"
    assert traceback is not None
