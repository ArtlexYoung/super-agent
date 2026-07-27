from __future__ import annotations

import argparse
import json
from pathlib import Path

from capability.defaults import create_progressive_skill_disclosure
from runtime.config import AgentConfig
from skill.kinds.model import (
    ModelProfile,
    model_profile_to_dict,
    read_model_profiles,
    select_default_model_profile,
)


def configure_models_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="models_command")
    list_parser = subparsers.add_parser(
        "list",
        help="list model Skills or zero-configuration environment profiles",
    )
    list_parser.add_argument("--config")
    list_parser.add_argument("--output", choices=["text", "json"], default="text")
    resolve_parser = subparsers.add_parser(
        "resolve",
        help="show the default model profile selected for this project",
    )
    resolve_parser.add_argument("--config")
    resolve_parser.add_argument("--output", choices=["text", "json"], default="text")


def run_models_command(args: argparse.Namespace) -> int:
    config = _load_config(getattr(args, "config", None))
    profiles = _read_configured_model_profiles(config)
    output = getattr(args, "output", "text")
    if args.models_command == "list":
        return _print_model_profiles(config, profiles, output)
    if args.models_command in {None, "resolve"}:
        return _print_selected_model(config, profiles, output)
    raise ValueError(f"unknown models command: {args.models_command}")


def _load_config(path: str | None) -> AgentConfig:
    return (
        AgentConfig.load_automatically()
        if path is None
        else AgentConfig.load_from_file(Path(path))
    )


def _read_configured_model_profiles(config: AgentConfig) -> list[ModelProfile]:
    disclosure = create_progressive_skill_disclosure(config)
    index = disclosure.prepare_skill_index()
    return read_model_profiles(disclosure, index)


def _print_model_profiles(
    config: AgentConfig,
    profiles: list[ModelProfile],
    output: str,
) -> int:
    if output == "json":
        print(
            json.dumps(
                {
                    "schema_version": 2,
                    "config_path": str(config.source),
                    "models": [model_profile_to_dict(profile) for profile in profiles],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    for profile in profiles:
        _print_model_profile(profile)
    return 0


def _print_selected_model(
    config: AgentConfig,
    profiles: list[ModelProfile],
    output: str,
) -> int:
    selected = select_default_model_profile(profiles)
    data = {
        "schema_version": 2,
        "config_path": str(config.source),
        "model": model_profile_to_dict(selected),
    }
    if output == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _print_model_profile(selected, prefix="selected")
    print(f"config\t{config.source}")
    return 0


def _print_model_profile(profile: ModelProfile, prefix: str = "profile") -> None:
    data = model_profile_to_dict(profile)
    print(
        f"{prefix}\t{profile.key}\t{data['provider']}\t{profile.model}"
        f"\tready={str(data['ready']).lower()}"
        f"\tdefault={str(profile.default).lower()}"
        f"\tsource={profile.source}"
        f"\tbase_url={data['base_url'] or ''}"
        f"\tapi_key_env={data['api_key_env'] or ''}"
    )
