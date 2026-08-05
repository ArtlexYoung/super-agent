# Local Release

Releases stay below `1.0` until the project release gate is intentionally changed. The
process is local and does not push or modify remote history.

## Environment

Use Homebrew-managed Python 3.11 and the existing Node installation:

```bash
brew install python@3.11
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
pnpm --dir web install --frozen-lockfile
```

## Checks

Replace `0.1.14` with the version being released:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests \
  .venv/bin/python -m unittest discover -s tests -p 'test_*.py'
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/python -m compileall -q src
.venv/bin/python scripts/verify_release.py --version 0.1.14
git diff --check
pnpm --dir web typecheck
pnpm --dir web lint
pnpm --dir web build
```

The release script is read-only. It checks the version in Python, TOML, and Web package
metadata, the dependency-free default, source layout, wheel roots, README link, and the
source size gate.

## Commit

Update `pyproject.toml`, `src/core/__init__.py`, and `web/package.json` together. Keep
unrelated working-tree changes out of the release commit:

```bash
git add -A -- . ':(exclude).gitignore'
git diff --cached --check
git commit -m "release: v0.1.14"
git tag v0.1.14
```
