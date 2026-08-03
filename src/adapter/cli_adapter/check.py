"""Read-only checks for the configuration used by the CLI."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from adapter.cli_adapter import load_common_config
from core.skill_use.defaults import create_default_skill_handlers, create_skills
from core.skill_use.models import (
    model_profile_is_ready,
    read_model_profiles,
    select_default_model_profile,
)


def configure_check_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config")
    parser.add_argument("--output", choices=("text", "json"), default="text")


def run_check_command(args: argparse.Namespace) -> int:
    checks: list[dict[str, object]] = []
    stage = "configuration"
    try:
        config = load_common_config(
            None if args.config is None else Path(args.config)
        )
        source = str(config.source) if config.source.is_file() else "built-in defaults"
        checks.append(_check("configuration", True, source))

        stage = "skills"
        skills = create_skills(
            config,
            handlers=create_default_skill_handlers(),
            include_freshness=False,
        )
        selected = skills.index.resolve_skill_dependencies(config.agent.skills)
        checks.append(
            _check(
                "skills",
                True,
                f"{len(skills.index.entries)} available, {len(selected)} configured",
            )
        )

        stage = "model"
        profiles = read_model_profiles(skills, os.environ)
        default = select_default_model_profile(profiles)
        ready = model_profile_is_ready(default, os.environ)
        requirement = default.connection.api_key_env
        detail = f"{default.key} -> {default.connection.provider}/{default.model}"
        if not ready and requirement is not None:
            detail += f"; missing {requirement}"
        checks.append(_check("model", ready, detail))
    except Exception as error:
        checks.append(
            _check(
                stage,
                False,
                f"{type(error).__name__}: {error}",
            )
        )

    result = {"ok": all(bool(item["ok"]) for item in checks), "checks": checks}
    if args.output == "json":
        print(json.dumps(result, ensure_ascii=False))
    else:
        for item in checks:
            status = "OK" if item["ok"] else "FAIL"
            print(f"{status}  {item['name']}: {item['detail']}")
        if not result["ok"]:
            print("Fix the failed check, then run `super-agent check` again.")
    return 0 if result["ok"] else 1


def _check(name: str, ok: bool, detail: str) -> dict[str, object]:
    return {"name": name, "ok": ok, "detail": detail}
