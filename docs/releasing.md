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

Replace `0.1.59` with the version being released:

```bash
.venv/bin/python scripts/verify_release.py --version 0.1.59 --full --web
```

Static mode is read-only. `--full` additionally runs all Python tests, compileall, diff
validation, and the committed offline benchmark in a temporary directory. `--web` explicitly
adds pnpm typecheck, lint, and build; a missing tool or failed command stops the gate.

## Commit

Update `pyproject.toml`, `src/core/__init__.py`, and `web/package.json` together. Keep
unrelated working-tree changes out of the release commit:

```bash
git add -A -- . ':(exclude).gitignore' ':(exclude)README.md' ':(exclude)README_cn.md'
git diff --cached --check
git commit -m "refactor(adapter): release v0.1.59 with clear I/O owners"
```
