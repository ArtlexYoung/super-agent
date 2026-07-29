from __future__ import annotations

import argparse
import json
import sys

from adapter.cli_adapter import load_agent_config, load_runtime_store
from skill.runners.defaults import create_progressive_skill_disclosure
from core.config import AgentConfig
from core.run import LOCAL_USER_ID
from core.task.actions import ActionRules
from skill.kinds.model import (
    ModelProfile,
    model_profile_to_dict,
    read_model_profiles,
    select_default_model_profile,
)
from skill.kinds.model_management import (
    ModelSkillManager,
    model_skill_input_from_dict,
)


def configure_models_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="models_command")
    list_parser = subparsers.add_parser(
        "list",
        help="list model Skills or zero-configuration environment profiles",
    )
    list_parser.add_argument("--config")
    list_parser.add_argument("--user-id", default=LOCAL_USER_ID)
    list_parser.add_argument("--output", choices=["text", "json"], default="text")
    resolve_parser = subparsers.add_parser(
        "resolve",
        help="show the default model profile selected for this project",
    )
    resolve_parser.add_argument("--config")
    resolve_parser.add_argument("--user-id", default=LOCAL_USER_ID)
    resolve_parser.add_argument("--output", choices=["text", "json"], default="text")
    save_parser = subparsers.add_parser(
        "save",
        help="create or update one model Skill from JSON stdin",
    )
    _add_management_arguments(save_parser)
    save_parser.add_argument("--request-stdin", action="store_true", required=True)
    remove_parser = subparsers.add_parser("remove", help="remove one model Skill")
    _add_management_arguments(remove_parser)
    remove_parser.add_argument("--name", required=True)


def run_models_command(args: argparse.Namespace) -> int:
    config = load_agent_config(getattr(args, "config", None))
    output = getattr(args, "output", "text")
    if args.models_command == "save":
        return _save_model_skill(config, args.user_id, output)
    if args.models_command == "remove":
        return _remove_model_skill(config, args.user_id, args.name, output)
    profiles = _read_configured_model_profiles(
        config,
        getattr(args, "user_id", LOCAL_USER_ID),
    )
    if args.models_command == "list":
        return _print_model_profiles(config, profiles, output)
    if args.models_command in {None, "resolve"}:
        return _print_selected_model(config, profiles, output)
    raise ValueError(f"unknown models command: {args.models_command}")


def _read_configured_model_profiles(
    config: AgentConfig,
    user_id: str,
) -> list[ModelProfile]:
    store = load_runtime_store(config, user_id)
    disclosure = create_progressive_skill_disclosure(config, store=store)
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


def _save_model_skill(config: AgentConfig, user_id: str, output: str) -> int:
    request = model_skill_input_from_dict(json.loads(sys.stdin.read()))
    profile = _create_model_skill_manager(config, user_id).save_model_skill(request)
    return _print_model_change(profile, output, "saved")


def _remove_model_skill(
    config: AgentConfig,
    user_id: str,
    name: str,
    output: str,
) -> int:
    _create_model_skill_manager(config, user_id).remove_model_skill(name)
    data = {"schema_version": 1, "name": name, "removed": True}
    if output == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Removed model Skill: model:{name}")
    return 0


def _print_model_change(profile: ModelProfile, output: str, action: str) -> int:
    data = {
        "schema_version": 1,
        "action": action,
        "model": model_profile_to_dict(profile),
    }
    if output == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_model_profile(profile, prefix=action)
    return 0


def _create_model_skill_manager(
    config: AgentConfig,
    user_id: str,
) -> ModelSkillManager:
    store = load_runtime_store(config, user_id)
    return ModelSkillManager(
        config,
        store,
        ActionRules(),
    )


def _add_management_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True)
    parser.add_argument("--user-id", default=LOCAL_USER_ID)
    parser.add_argument("--output", choices=["text", "json"], default="text")
