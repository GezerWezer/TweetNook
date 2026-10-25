# Publishing

Use this for a new TweetNook version. A release is finished when the intended package, GitHub release and demo are verified, not when the tag has merely been created.

## Prepare

- [ ] Confirm the intended base branch and release scope. Run `git status -sb` and inspect recent commits; leave unrelated work alone.
- [ ] Update the version in `pyproject.toml` and `tweetnook/__init__.py`. Check any version-bearing build/demo inputs affected by the release.
- [ ] Add the matching dated section to `CHANGELOG.md`, using Added, Changed and Fixed where relevant. Describe the resulting behavior for users.
- [ ] Update the README, user/developer guides and optional-feature instructions for the release.
- [ ] Rebuild browser dependencies/CSS when their sources changed, and retain the bundled licenses.
- [ ] Regenerate `tweetnook/web/static/setup-help.json` if the cookie guide changed.

## Validate

- [ ] Run the Python suite, lint and format checks in the intended dependency environment.
- [ ] Run the relevant JavaScript harnesses and browser smoke checks, including phone/desktop behavior for UI changes.
- [ ] Check command help, local links, fenced examples, config defaults and optional extras.
- [ ] Validate the installer and managed-service paths affected by this release.
- [ ] Build distributions and run Twine validation.
- [ ] Inspect distribution contents for required web assets, docs and licenses; check for excluded data and secrets.
- [ ] Install the wheel in a fresh environment with temporary XDG roots. Check version/help and browser-first Setup, then test any affected extras.
- [ ] Build the static demo with the repository base path and version; verify its routes, local media and disabled server actions.

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
uvx twine check dist/*
```

## Commit and tag

- [ ] Review `git status -sb`, the release diff and validation evidence.
- [ ] Stage only the release files by explicit path; inspect staged names and the full staged diff.
- [ ] Commit the release unit and confirm version/changelog agreement at that commit.
- [ ] Create the intended `vVERSION` tag at the reviewed release commit. Confirm the tag target before pushing it.
- [ ] Push the authorized branch/tag to the intended remote. A `v*` tag starts the release workflow.

## After publishing

- [ ] Verify build and trusted PyPI publication completed for the intended version.
- [ ] Verify the GitHub Release notes, version/title, distributions and attached installer.
- [ ] Verify the published PyPI metadata and install the published package in a clean environment.
- [ ] Verify GitHub Pages deployment and the public demo's base-path navigation, media and version.
- [ ] Record the release/tag URLs, published version and validation results in the worklog.

If a step fails after publication begins, inspect completed uploads before retrying. Follow the [release guide](development/release.md) and workflow rather than replacing already published artifacts blindly.

## Publisher setup

Use patch versions for fixes, performance/docs releases and small UX changes; minor versions for new commands or substantial archive/search features; major versions for intentional breaking changes with upgrade guidance. Keep `uv.lock`'s local package version metadata in agreement when applicable.

The changelog uses topmost dated release sections and version links, with validation where relevant. Don't keep an in-progress Unreleased section or add Highlights to the published notes. Create an annotated release tag after checking the intended remote base and existing tags.

PyPI Trusted Publishing is configured for project `tweetnook`, owner `gezerwezer`, repository `tweetnook`, workflow `release.yml`, environment `pypi`. Use a required reviewer for the protected environment and verify that setup before the first publication. A pending publisher is managed through [PyPI publishing settings](https://pypi.org/manage/account/publishing/); no PyPI token belongs in GitHub Secrets for this workflow.

Include `bash -n install.sh` and the installer/system-install/service suites in release checks. Smoke-test Linux root/sudo service installation, personal macOS installation and updates of an existing service when affected. Verify the exact built distributions rather than picking up stale files from a prior build.
