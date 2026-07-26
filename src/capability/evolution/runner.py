"""Subprocess entry point for isolated Capability behavior evaluation."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from capability.package import validate_capability_directory


def main(arguments: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if arguments is None else arguments)
    if len(values) != 1:
        print("usage: python -m capability.evolution.runner <candidate-directory>", file=sys.stderr)
        return 2
    try:
        request = _read_request()
        captured_output = io.StringIO()
        captured_error = io.StringIO()
        with redirect_stdout(captured_output), redirect_stderr(captured_error):
            installed = validate_capability_directory(Path(values[0]))
            evaluator = getattr(installed.implementation, "evaluate_capability", None)
            if not callable(evaluator):
                raise TypeError("Capability candidate must implement evaluate_capability")
            result = evaluator(request["input"])
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "output": result,
                    "captured_stdout": captured_output.getvalue(),
                    "captured_stderr": captured_error.getvalue(),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


def _read_request() -> dict[str, object]:
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict) or set(data) != {"schema_version", "input"}:
        raise ValueError("Capability evaluation request fields do not match schema v1")
    if data["schema_version"] != 1:
        raise ValueError("unsupported Capability evaluation request schema")
    if not isinstance(data["input"], dict):
        raise TypeError("Capability evaluation input must be a JSON object")
    return data


if __name__ == "__main__":
    raise SystemExit(main())
