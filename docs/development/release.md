# Releases

Release work follows the [publishing checklist](../PUBLISH.md). Keep the package version, changelog, docs, generated browser assets and tag in agreement before publication.

## Build inputs

`pyproject.toml` uses Hatchling and defines the package, runtime dependencies, extras and source-distribution file list. `tweetnook/__init__.py` also carries the version. Wheels ship the Python application and bundled web assets; the source distribution includes maintained docs and the asset-build inputs.

Node isn't a runtime dependency. If browser dependency sources changed, build `dev/web` before packaging and include the generated vendor/CSS files and licenses. Regenerate Setup help when the cookie guide changes.

## Validation

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python scripts/build_setup_help.py
uv build
uvx twine check dist/*
```

Review expected artifacts in `dist/`, inspect wheel/sdist contents and install the wheel into a clean environment. Verify `tweetnook --version`, representative `--help`, server-first Setup, and optional-dependency behavior. Use temporary XDG roots so packaging checks don't change a personal archive.

## Automation

A pushed `v*` tag triggers `.github/workflows/release.yml`:

1. Build distributions and run Twine validation.
2. Publish through the protected PyPI environment using trusted publishing.
3. Create a GitHub Release with distributions and `install.sh`.
4. Build and deploy the static demonstration site to GitHub Pages.

Release notes are extracted from the matching changelog version's Added, Changed and Fixed sections. The workflow rejects an empty extraction and doesn't publish a Highlights section. Include a comparison link to the preceding tag when one exists.

## After publishing

Confirm the intended version appears on PyPI and the GitHub release, distribution assets and installer are attached, the workflow completed, and the public demo loads under its repository base path. Test a clean install from the published package separately from a source checkout.

If publication partly succeeds, inspect which registry/artifact step completed before retrying. Don't assume an upload can be overwritten or replace an existing version's contents. Keep release credentials outside the repository.
