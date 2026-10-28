"""Report source files over the size budget and imports that point up the layer stack.

Both checks report by default; --strict, which CI runs, fails on either. A third list,
private names imported from another module, is only ever reported.

    uv run python scripts/check_structure.py [--strict]

Size: files over WARN_LINES are listed, and files over LIMIT_LINES fail under --strict.
A file in SIZE_EXEMPT fails only if it grows past the ceiling recorded there.

Private imports: a module that imports another module's underscore names often shares one
concern with it, so a split between them may cut in the wrong place. A helper that other
modules are meant to use should be named without the underscore.

Imports: top-level packages are ranked interface (CLI, web) > feature > storage > core and
may only import their own layer or a lower one. Unlisted packages count as features.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WARN_LINES = 800
LIMIT_LINES = 1200

SIZE_GLOBS = (
    "tweetnook/**/*.py",
    "tweetnook/web/static/js/**/*.js",
    "tweetnook/web/static/css/**/*.css",
    "tweetnook/web/**/*.html",
    "tests/**/*.py",
    "tests/js/**/*.cjs",
    "scripts/**/*.py",
)
# Vendored and generated files aren't edited by hand, so the budget doesn't apply.
SIZE_EXCLUDED = (
    "tweetnook/web/static/vendor/",
    "tweetnook/web/static/css/tailwind.css",
)
# Over the limit for a reason a move can't fix. Each ceiling is the file's size when it was
# exempted, so it can shrink but not grow; split the file instead of raising the number.
SIZE_EXEMPT = {
    # tag_media_tweets is one function of about 1,000 lines. It needs restructuring into
    # stages, which changes the paid Gemini path and deserves its own review.
    "tweetnook/tagging/tagger.py": 1887,
}

# config belongs to core but stays a top-level module: install.sh imports it by name.
CORE = {"core", "config"}
STORAGE = {"storage"}
INTERFACE = {"__main__", "cli", "cli_service", "system_install", "web"}
LAYERS = {"core": 0, "storage": 1, "feature": 2, "interface": 3}


def layer(module: str) -> str:
    parts = module.split(".")
    name = parts[1] if len(parts) > 1 else ""
    if name in INTERFACE:
        return "interface"
    if name in STORAGE:
        return "storage"
    if name in CORE:
        return "core"
    return "feature"


def oversized_files() -> list[tuple[int, str]]:
    found: dict[str, int] = {}
    for pattern in SIZE_GLOBS:
        for path in ROOT.glob(pattern):
            relative = path.relative_to(ROOT).as_posix()
            if "__pycache__" in relative or relative.startswith(SIZE_EXCLUDED):
                continue
            lines = len(path.read_text(encoding="utf-8").splitlines())
            if lines > WARN_LINES:
                found[relative] = lines
    return sorted(((lines, name) for name, lines in found.items()), reverse=True)


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _import_source(node: ast.ImportFrom, path: Path, module: str) -> str:
    if not node.level:
        return node.module or ""
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    base = package.split(".")[: len(package.split(".")) - node.level + 1]
    return ".".join([*base, *([node.module] if node.module else [])])


def _imported_modules(path: Path, module: str) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            source = _import_source(node, path, module)
            # `from tweetnook import sync` imports a module by its imported name.
            imported.update(f"{source}.{alias.name}" for alias in node.names)
    # Layers are decided by the top-level module, so report edges at that level.
    return {".".join(name.split(".")[:2]) for name in imported if name.startswith("tweetnook.")}


def upward_imports() -> list[tuple[str, str]]:
    violations = []
    for path in sorted((ROOT / "tweetnook").rglob("*.py")):
        module = _module_name(path)
        for target in sorted(_imported_modules(path, module)):
            if LAYERS[layer(target)] > LAYERS[layer(module)]:
                violations.append((module, target))
    return violations


def private_imports() -> list[tuple[str, str]]:
    found = []
    for path in sorted((ROOT / "tweetnook").rglob("*.py")):
        module = _module_name(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            source = _import_source(node, path, module)
            if not source.startswith("tweetnook"):
                continue
            for alias in node.names:
                if alias.name.startswith("_") and not alias.name.startswith("__"):
                    found.append((module, f"{source}.{alias.name}"))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero on files over the limit or upward imports",
    )
    args = parser.parse_args(argv)

    oversized = oversized_files()
    over_limit = [
        (lines, name) for lines, name in oversized if lines > SIZE_EXEMPT.get(name, LIMIT_LINES)
    ]
    print(f"Files over {WARN_LINES} lines ({len(over_limit)} over the {LIMIT_LINES}-line limit):")
    for lines, name in oversized:
        if (lines, name) in over_limit:
            mark = "LIMIT"
        elif name in SIZE_EXEMPT:
            mark = "exempt"
        else:
            mark = "warn"
        print(f"  {mark:<6} {lines:>6}  {name}")

    violations = upward_imports()
    print(f"\nImports that point up the layer stack ({len(violations)}):")
    for module, target in violations:
        print(f"  {module} ({layer(module)}) -> {target} ({layer(target)})")

    private = private_imports()
    print(f"\nPrivate names imported from another module ({len(private)}, report only):")
    for module, name in private:
        print(f"  {module} <- {name}")

    if args.strict:
        return 1 if over_limit or violations else 0
    if over_limit or violations:
        print("\nReport only: run with --strict to fail on these.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
