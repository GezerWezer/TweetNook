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
- Current source version at audit: `0.0.9`
- Build backend: Hatchling
- Python: ≥3.12
- Console script: `tweetnook = tweetnook.cli:app`
- License: Apache-2.0

Known metadata mismatches:

- `readme = "README.md"`, not staged `README.md`;
- the original packaged README still advertises stale FastAPI/Vue and HTML-
  export behavior;
- `lancedb` and `pyarrow` remain mandatory core dependencies even though the
  current runtime uses them only for one-way legacy archive migration;
- public PyPI can resolve a different upstream version/feature set;
- original changelog/docs contain stale runtime claims.

The new docs enter the distribution only after build configuration is updated
and the resulting artifacts are checked.

## Pre-release provenance decision

Before any registry upload, choose and document one path:

1. coordinate with the existing public project and intentionally release the
   fork as its continuation; or
2. choose a distinct distribution name/URLs/repository issue tracker while
   preserving or deliberately renaming the console command.

Then update every package/repository/install link consistently. Do not upload a
fork version into a namespace you do not control.

## Promotion of staged documentation

When the rewrite is approved:

- replace/update the real `README.md` deliberately;
- move or merge `docs/` into the intended `docs/` layout;
- update every internal relative link after the move;
- update `docs/README.md` or replace its index;
- change `pyproject.toml` `readme` and sdist includes;
- remove or clearly quarantine obsolete docs from primary navigation;
- rebuild and inspect wheel/sdist rendering/content;
- run a concrete local-link/anchor checker over every promoted Markdown file
  (the staged rewrite does not yet ship such a script).

Do not leave two apparently authoritative manuals after promotion.

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
- [ ] Add a topmost dated changelog section with user-visible behavior and
  validation; remove/resolve contradictory unreleased history.
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
verifying. Create a GitHub Release from the corresponding changelog section;
tags alone do not create the release-page entry.

The commands above describe mechanics, not authorization. Publishing, pushing,
tagging, and release creation are external state changes and require the release
owner's direction.

## GitHub Actions Trusted Publishing

The active release workflow is
[`../../.github/workflows/release.yml`](../../.github/workflows/release.yml).
It builds and checks the wheel/source distribution on `v*` tag pushes, stores
the artifacts, and publishes them through the `pypi` GitHub environment using
PyPI Trusted Publishing. Configure the matching pending publisher on PyPI with
owner `gezerwezer`, repository `tweetnook`, workflow `release.yml`, and
environment `pypi`. No PyPI API token or GitHub secret is required.

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
