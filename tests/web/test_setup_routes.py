from __future__ import annotations

import io
import subprocess
import tomllib
import zipfile
from pathlib import Path
from types import SimpleNamespace

from tweetnook import config as config_module
from tweetnook.config import XDGPaths, ensure_paths
from tweetnook.web.deps import server_state
from tweetnook.web.notices import NoticeStore
from tweetnook.web.routes import setup
from tweetnook.web.setup_coordinator import SetupCoordinator


class SetupStore:
    def __init__(self, *, imported: bool = False, pending: int = 0) -> None:
        self.imported = imported
        self.pending = pending

    def has_completed_archive_import(self) -> bool:
        return self.imported

    def count_incomplete_initial_enrichment(self) -> int:
        return self.pending


def _paths(tmp_path: Path) -> XDGPaths:
    return ensure_paths(
        XDGPaths(
            config_dir=tmp_path / "config",
            data_dir=tmp_path / "data",
            cache_dir=tmp_path / "cache",
        )
    )


def _install_coordinator(paths):
    server_state["config"] = config_module.AppConfig()
    server_state["setup_coordinator"] = SetupCoordinator(
        paths, server_state, lambda: None, lambda: None, NoticeStore(paths.notices_file)
    )


def _zip_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("data/manifest.js", "window.__THAR_CONFIG = {};\n")
    return output.getvalue()


def test_setup_status_masks_auth_and_reports_import_readiness(
    monkeypatch, tmp_path: Path, make_web_client
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(
        setup,
        "load_config",
        lambda: (
            config_module.AppConfig.model_validate(
                {"auth": {"auth_token": "secret", "ct0": "csrf", "user_id": "42"}}
            ),
            paths,
        ),
    )
    server_state.update({"paths": paths, "store": SetupStore()})
    client = make_web_client(setup.router)

    response = client.get("/api/setup")

    assert response.status_code == 200
    payload = response.json()
    assert payload["auth"]["configured"] is True
    assert payload["auth"]["verified"] is False
    assert payload["auth"]["values"]["auth_token"] == "********"
    assert payload["archive"]["imported"] is False
    assert payload["archive"]["enriched"] is False
    assert payload["archive"]["warnings"]


def test_archive_upload_import_and_clear_are_real_guarded_actions(
    tmp_path, make_web_client
) -> None:
    paths = _paths(tmp_path)
    store = SetupStore(imported=True, pending=7)

    class Supervisor:
        active = False
        active_run_id = None
        active_kind = None

        def _external_activity_is_running(self):
            return False

        @staticmethod
        def start(**kwargs):
            kwargs.pop("run_id")
            assert kwargs == {
                "kind": "import",
                "cli_args": ["import", "x-archive", str(paths.staged_archive_file), "--offline"],
                "origin": "web",
                "title": "tweetnook import x-archive",
            }
            return {"started": True, "kind": "import", "run_id": "run", "pid": 42}

    server_state.update({"paths": paths, "store": store, "job_supervisor": Supervisor()})
    _install_coordinator(paths)
    client = make_web_client(setup.router)

    invalid = client.put("/api/setup/archive", content=b"not a zip")
    assert invalid.status_code == 422
    assert not paths.staged_archive_file.exists()

    uploaded = client.put(
        "/api/setup/archive",
        content=_zip_bytes(),
        headers={"Content-Type": "application/zip"},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["uploaded"] is True
    assert uploaded.json()["pending_enrichment"] == 7

    started = client.post("/api/setup/archive/import")
    assert started.status_code == 202
    assert started.json()["kind"] == "import"

    cleared = client.delete("/api/setup/archive")
    assert cleared.status_code == 200
    assert cleared.json()["uploaded"] is False
    assert not paths.staged_archive_file.exists()


def test_setup_import_can_skip_all_live_followup(tmp_path, make_web_client) -> None:
    paths = _paths(tmp_path)

    class Supervisor:
        active = False
        active_run_id = None
        active_kind = None

        def _external_activity_is_running(self):
            return False

        @staticmethod
        def start(**kwargs):
            assert kwargs["cli_args"] == [
                "import",
                "x-archive",
                str(paths.staged_archive_file),
                "--offline",
            ]
            return {"started": True, "kind": "import", "run_id": kwargs["run_id"]}

    server_state.update({"paths": paths, "store": SetupStore(), "job_supervisor": Supervisor()})
    _install_coordinator(paths)
    client = make_web_client(setup.router)
    assert client.put("/api/setup/archive", content=_zip_bytes()).status_code == 200
    assert client.post("/api/setup/archive/import?offline=true").status_code == 202


def test_setup_auth_is_saved_only_after_successful_candidate_probe(
    monkeypatch, tmp_path: Path, make_web_client
) -> None:
    paths = _paths(tmp_path)
    paths.config_file.write_text(
        '[auth]\nauth_token = "secret"\nct0 = "csrf"\nuser_id = ""\n',
        encoding="utf-8",
    )

    def load_test_config():
        raw = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))
        return config_module.AppConfig.model_validate(raw), paths

    monkeypatch.setattr(setup, "load_config", load_test_config)
    probe_results = [
        subprocess.CompletedProcess([], 2, "", "candidate rejected"),
        subprocess.CompletedProcess([], 0, "bookmarks: ready\nlikes: ready\n", ""),
    ]
    probe_envs: list[dict[str, str]] = []
    probe_configs: list[dict[str, object]] = []

    def run_probe(*args, **kwargs):
        probe_envs.append(kwargs["env"])
        config_path = Path(kwargs["env"]["XDG_CONFIG_HOME"]) / "tweetnook" / "config.toml"
        probe_configs.append(tomllib.loads(config_path.read_text(encoding="utf-8")))
        return probe_results.pop(0)

    monkeypatch.setattr(setup.subprocess, "run", run_probe)
    verification = SimpleNamespace(auth_verified=False)

    def mark_verified():
        verification.auth_verified = True

    server_state.update(
        {
            "paths": paths,
            "store": SetupStore(),
            "setup_coordinator": SimpleNamespace(
                state=verification,
                auth_was_verified=mark_verified,
            ),
        }
    )
    client = make_web_client(setup.router)

    failed = client.put(
        "/api/setup/auth",
        json={
            "auth_token": "bad-token",
            "ct0": "bad-csrf",
            "user_id": "99",
        },
    )
    assert failed.status_code == 422
    assert "candidate rejected" in failed.json()["detail"]
    assert verification.auth_verified is False
    config, _ = load_test_config()
    assert config.auth.auth_token == "secret"
    assert config.auth.ct0 == "csrf"
    assert config.auth.user_id == ""

    saved = client.put(
        "/api/setup/auth",
        json={
            "auth_token": "********",
            "ct0": "********",
            "user_id": "42",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert saved.json()["verified"] is True
    config, _ = load_test_config()
    assert config.auth.auth_token == "secret"
    assert config.auth.ct0 == "csrf"
    assert config.auth.user_id == "42"
    assert probe_envs[0]["XDG_CONFIG_HOME"] != str(paths.config_dir.parent)
    assert "TWEETNOOK_AUTH_TOKEN" not in probe_envs[0]
    assert "TWEETNOOK_CT0" not in probe_envs[0]
    assert "TWEETNOOK_USER_ID" not in probe_envs[0]
    assert probe_configs[0]["auth"] == {
        "auth_token": "bad-token",
        "ct0": "bad-csrf",
        "user_id": "99",
    }
    assert probe_configs[1]["auth"] == {
        "auth_token": "secret",
        "ct0": "csrf",
        "user_id": "42",
    }

    rejected = client.put("/api/setup/auth", json={"browser": "firefox"})
    assert rejected.status_code == 422
