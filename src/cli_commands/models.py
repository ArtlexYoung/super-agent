from __future__ import annotations

import argparse
import json
from pathlib import Path

from provider.discovery import (
    ModelResolution,
    discover_model_candidates,
    model_resolution_to_dict,
    resolve_model_settings,
)
from runtime.config import AgentConfig


def configure_models_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="models_command")
    list_parser = subparsers.add_parser(
        "list",
        help="list model configurations discovered from the environment",
    )
    list_parser.add_argument("--output", choices=["text", "json"], default="text")
    resolve_parser = subparsers.add_parser(
        "resolve",
        help="show the model configuration selected for this project",
    )
    resolve_parser.add_argument("--config")
    resolve_parser.add_argument("--output", choices=["text", "json"], default="text")


def run_models_command(args: argparse.Namespace) -> int:
    if args.models_command == "list":
        return _list_discovered_models(args.output)
    if args.models_command in {None, "resolve"}:
        return _show_resolved_model(getattr(args, "config", None), getattr(args, "output", "text"))
    raise ValueError(f"unknown models command: {args.models_command}")


def _list_discovered_models(output: str) -> int:
    candidates = discover_model_candidates()
    if output == "json":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "models": [model_resolution_to_dict(item) for item in candidates],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    for candidate in candidates:
        _print_model_resolution(candidate)
    return 0


def _show_resolved_model(config_path: str | None, output: str) -> int:
    config = (
        AgentConfig.load_automatically()
        if config_path is None
        else AgentConfig.load_from_file(Path(config_path))
    )
    resolution = resolve_model_settings(config.model)
    data = {
        "schema_version": 1,
        "config_path": str(config.source),
        "model": model_resolution_to_dict(resolution),
    }
    if output == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _print_model_resolution(resolution, prefix="selected")
    print(f"config\t{config.source}")
    return 0


def _print_model_resolution(
    resolution: ModelResolution,
    prefix: str = "candidate",
) -> None:
    settings = resolution.settings
    print(
        f"{prefix}\t{settings.provider}\t{settings.model}"
        f"\tready={str(resolution.ready).lower()}"
        f"\tsource={resolution.source}"
        f"\tbase_url={settings.base_url or ''}"
        f"\tapi_key_env={settings.api_key_env or ''}"
    )
