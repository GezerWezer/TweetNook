# Release

[Development guide](README.md) · [Testing](testing.md) ·
[Documentation audit](documentation-audit.md)

Follow the repository's [release punch list](../../docs/PUBLISH.md) when preparing
a version. It covers version metadata, the changelog, validation, tags, PyPI,
and GitHub bookkeeping. This page adds packaging checks for the revised docs.

Before publishing, align the intended repository and distribution with the
metadata in `pyproject.toml`. The current metadata and installation instructions
use the `gezerwezer/tweetnook` checkout; the original upstream project remains
linked separately in the documentation audit.

## Current release metadata

- Distribution: `tweetnook`
- Version sources:
  - `pyproject.toml`
  - `tweetnook/__init__.py`
- Current source version: `0.1.3`
- Build backend: Hatchling
- Python: ≥3.12
- Console script: `tweetnook = tweetnook.cli:app`
- License: Apache-2.0

The `gezerwezer/tweetnook` repository and `tweetnook` PyPI project are the
release targets. Tagged releases publish through the protected GitHub Actions
environment described below.

## Versioning

Use semantic-style increments:

- patch: fixes, performance, docs-only release, low-risk UX cleanup;
- minor: new commands/flags/archive features or substantial search/storage work;
- major: intentional breaking CLI/storage/workflow changes with migration notes.

Update both version sources in one commit. Ensure `uv.lock`'s local package
metadata reflects the release when applicable.

## Release preparation checklist

- [ ] Begin from the intended release branch and a clean worktree.
- [ ] Fetch tags and fast-forward from the chosen release remote.
- [ ] Resolve distribution/repository/PyPI ownership and metadata.
- [ ] Pick version and update both version sources.
- [ ] Add a topmost dated changelog section with concise `Changed` and `Fixed`
  bullets, an `Added` section when applicable, user-visible behavior, and
  validation; do not add a `Highlights` section; remove/resolve contradictory
  unreleased history.
- [ ] Update installation, CLI, supported-platform, security, and migration docs.
- [ ] Update documentation index and packaging includes.
- [ ] Add a concise `WORKLOG.md` release record.
- [ ] Run full validation below.
- [ ] Build new artifacts; do not reuse an old `dist/` artifact.
- [ ] Inspect wheel and sdist contents/metadata/rendering.
- [ ] Smoke-test the built wheel in isolation.
- [ ] Stage only named release files and review the staged diff.
- [ ] Commit release metadata, create annotated tag, and push deliberately.
- [ ] Publish only after explicit authorization and namespace verification.
- [ ] Verify registry install, tag, GitHub Release, links, and clean tree.

## Validation commands

```bash
UV_CACHE_DIR=/tmp/tweetnook-uv-cache uv run ruff format --check
UV_CACHE_DIR=/tmp/tweetnook-uv-cache uv run ruff check
UV_CACHE_DIR=/tmp/tweetnook-uv-cache uv run pytest -q
node tests/js/test_web_assets.cjs
uv build --clear --out-dir dist/release-X.Y.Z
uvx --from twine twine check \
  dist/release-X.Y.Z/tweetnook-X.Y.Z.tar.gz \
  dist/release-X.Y.Z/tweetnook-X.Y.Z-py3-none-any.whl
uv run --isolated --no-project \
  --with dist/release-X.Y.Z/tweetnook-X.Y.Z-py3-none-any.whl -- \
  tweetnook --help
```

Also verify:

- core CLI/Web import with no automated-tagging extra;
- optional tagging import/routes with the extra;
- source-install instructions from a clean clone;
- generated Markdown links on GitHub;
- actual built package README and documentation content;
- no credentials/database/media/activity artifacts in source distributions.

## Tag and publish sequence

After release review, replace `X.Y.Z` with the version and `YOUR_RELEASE_BRANCH`
with the branch being published:

```bash
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin YOUR_RELEASE_BRANCH
git push origin vX.Y.Z
```

Publish exact fresh artifacts rather than a wildcard containing old versions:

```bash
uv publish \
  dist/release-X.Y.Z/tweetnook-X.Y.Z-py3-none-any.whl \
  dist/release-X.Y.Z/tweetnook-X.Y.Z.tar.gz
```

Use Twine only as an intentional fallback. Refresh registry resolver caches when
verifying. After the PyPI upload succeeds, the tagged workflow creates the
GitHub Release with the matching changelog `Added` section when applicable, plus
`Changed` and `Fixed`, a full-changelog comparison link, and the exact
wheel/source artifacts; review the release entry as needed.

The commands above describe mechanics, not authorization. Publishing, pushing,
tagging, and release creation are external state changes and require the release
owner's direction.

## GitHub Actions Trusted Publishing

The active release workflow is
[`../../.github/workflows/release.yml`](../../.github/workflows/release.yml).
It builds and checks the wheel/source distribution on `v*` tag pushes, stores
the artifacts, publishes them through the `pypi` GitHub environment using PyPI
Trusted Publishing, and creates a GitHub Release after the upload succeeds.
The release includes changelog `Added` notes when applicable, plus `Changed` and
`Fixed`, a full-changelog comparison link, and both distribution artifacts. It
does not generate a `Highlights` section.
Configure the matching pending publisher on PyPI with owner `gezerwezer`,
repository `tweetnook`, workflow `release.yml`, and environment `pypi`. No
PyPI API token or GitHub secret is required.

Protect the `pypi` GitHub environment with a required reviewer before releasing.

## Archive compatibility notes

Every release touching storage/import must document:

- source and target schema versions;
- automatic backup name/scope;
- whether migration is reversible;
- media/config/activity files outside the DB backup;
- legacy LanceDB limitations;
- owner/source merge behavior;
- validation and restore strategy.

Never describe a JSON export as a rollback artifact.
