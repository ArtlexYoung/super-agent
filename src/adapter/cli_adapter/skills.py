from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from adapter.cli_adapter.configuration import load_agent, load_common_config, load_event_store
from core.checks import ActionRules
from core.config import CommonConfig
from core.models import LOCAL_USER_ID
from skill.disclosure import ProgressiveDisclosureCore, skill_index_to_dict
from skill.runtime.handlers import (
    create_progressive_skill_disclosure,
    create_skills,
    load_configured_freshness_rules,
)
from skill.learning.freshness import calculate_skill_freshness
from skill.learning.records import read_evaluation_records
from skill.learning.update import SkillChangeCase
from skill.manifest import SkillManifest
from skill.runtime.package import SkillPackageManager, write_skill_lock_file
from skill.runtime.model_skills import ModelSkillManager, model_skill_input_from_dict
from skill.runtime.models import (
    ModelProfile,
    model_profile_to_dict,
    read_model_profiles,
    select_default_model_profile,
)


def configure_skills_parser(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction:
    subparsers = parser.add_subparsers(dest="skill_command")
    list_parser = subparsers.add_parser("list", help="list available skills")
    list_parser.add_argument("--common-config", default="common.toml")
    index_parser = subparsers.add_parser("index", help="print the central skill index as JSON")
    index_parser.add_argument("--common-config", default="common.toml")
    index_parser.add_argument("--output", choices=["json"], default="json")
    freshness_parser = subparsers.add_parser("freshness", help="show runtime skill freshness stats")
    freshness_parser.add_argument("--common-config", default="common.toml")
    validate_parser = subparsers.add_parser("validate", help="validate every skill manifest")
    validate_parser.add_argument("--common-config", default="common.toml")
    graph_parser = subparsers.add_parser("graph", help="resolve a skill dependency graph")
    _add_composition_arguments(graph_parser)
    changes_parser = subparsers.add_parser("changes", help="manage Skill changes")
    configure_skill_changes_parser(changes_parser)
    packages_parser = subparsers.add_parser("packages", help="manage Skill packages")
    configure_skill_packages_parser(packages_parser)
    models_parser = subparsers.add_parser("models", help="manage model Skills")
    configure_models_parser(models_parser)
    for command_parser in (
        list_parser,
        index_parser,
        freshness_parser,
        validate_parser,
        graph_parser,
    ):
        command_parser.add_argument("--user-id", default=LOCAL_USER_ID)
    return subparsers


def configure_skill_changes_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="skill_change_command")
    propose = subparsers.add_parser("propose", help="propose an isolated Skill change")
    _add_change_name_arguments(propose)
    test = subparsers.add_parser("test", help="test a change without applying it")
    _add_change_id_arguments(test)
    test.add_argument("--cases", required=True)
    apply = subparsers.add_parser("apply", help="apply a passing Skill change")
    _add_change_id_arguments(apply)
    undo = subparsers.add_parser("undo", help="undo an applied Skill change")
    _add_change_id_arguments(undo)
    list_changes = subparsers.add_parser("list", help="list proposed Skill changes")
    list_changes.add_argument("--common-config", default="common.toml")
    for command_parser in (propose, test, apply, undo, list_changes):
        command_parser.add_argument("--user-id", default=LOCAL_USER_ID)


def configure_skill_packages_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="skill_package_command")
    lock = subparsers.add_parser("lock", help="write a deterministic Skill lock")
    _add_composition_arguments(lock)
    lock.add_argument("--output", default="skill.lock")
    pack = subparsers.add_parser("pack", help="pack one Skill as a deterministic ZIP")
    pack.add_argument("--common-config", default="common.toml")
    pack.add_argument("--name", required=True)
    pack.add_argument("--output", required=True)
    install = subparsers.add_parser("install", help="install a local, ZIP, or Git Skill")
    _add_package_source_arguments(install)
    update = subparsers.add_parser("update", help="replace an installed Skill")
    _add_package_source_arguments(update)
    update.add_argument("--name", required=True)
    remove = subparsers.add_parser("remove", help="remove one installed Skill")
    remove.add_argument("--common-config", default="common.toml")
    remove.add_argument("--name", required=True)
    for command_parser in (lock, pack, install, update, remove):
        command_parser.add_argument("--user-id", default=LOCAL_USER_ID)


def configure_models_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="models_command")
    list_parser = subparsers.add_parser(
        "list",
        help="list model Skills or zero-configuration environment profiles",
    )
    _add_model_read_arguments(list_parser)
    resolve_parser = subparsers.add_parser(
        "resolve",
        help="show the default model profile selected for this project",
    )
    _add_model_read_arguments(resolve_parser)
    save_parser = subparsers.add_parser(
        "save",
        help="create or update one model Skill from JSON stdin",
    )
    _add_model_write_arguments(save_parser)
    save_parser.add_argument("--request-stdin", action="store_true", required=True)
    remove_parser = subparsers.add_parser("remove", help="remove one model Skill")
    _add_model_write_arguments(remove_parser)
    remove_parser.add_argument("--name", required=True)


def run_skills_command(args: argparse.Namespace) -> int:
    handlers = {
        "list": lambda: _list_skills(Path(args.common_config), args.user_id),
        "index": lambda: _print_skill_index(Path(args.common_config), args.user_id),
        "freshness": lambda: _show_skill_freshness(Path(args.common_config), args.user_id),
        "validate": lambda: _validate_skills(Path(args.common_config), args.user_id),
        "graph": lambda: _show_skill_graph(args),
        "changes": lambda: run_skill_changes_command(args),
        "packages": lambda: run_skill_packages_command(args),
        "models": lambda: run_models_command(args),
    }
    handler = handlers.get(args.skill_command)
    if handler is None:
        raise ValueError("skills command is required")
    return handler()


def run_skill_changes_command(args: argparse.Namespace) -> int:
    handlers = {
        "propose": lambda: _propose_skill_change(args),
        "test": lambda: _test_skill_change(args),
        "apply": lambda: _apply_skill_change(args),
        "undo": lambda: _undo_skill_change(args),
        "list": lambda: _list_skill_changes(args),
    }
    handler = handlers.get(args.skill_change_command)
    if handler is None:
        raise ValueError("skills changes command is required")
    return handler()


def run_skill_packages_command(args: argparse.Namespace) -> int:
    handlers = {
        "lock": lambda: _write_skill_lock(args),
        "pack": lambda: _pack_skill(args),
        "install": lambda: _install_skill(args),
        "update": lambda: _update_skill(args),
        "remove": lambda: _remove_skill(args),
    }
    handler = handlers.get(args.skill_package_command)
    if handler is None:
        raise ValueError("skills packages command is required")
    return handler()


def run_models_command(args: argparse.Namespace) -> int:
    config = load_common_config(getattr(args, "common_config", None))
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
    config: CommonConfig,
    user_id: str,
) -> list[ModelProfile]:
    store = load_event_store(config, user_id)
    return read_model_profiles(create_skills(config, store=store))


def _print_model_profiles(
    config: CommonConfig,
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
    config: CommonConfig,
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


def _save_model_skill(config: CommonConfig, user_id: str, output: str) -> int:
    request = model_skill_input_from_dict(json.loads(sys.stdin.read()))
    profile = _create_model_skill_manager(config, user_id).save_model_skill(request)
    return _print_model_change(profile, output, "saved")


def _remove_model_skill(
    config: CommonConfig,
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
    config: CommonConfig,
    user_id: str,
) -> ModelSkillManager:
    store = load_event_store(config, user_id)
    return ModelSkillManager(config, store, ActionRules())


def _list_skills(config_path: Path, user_id: str) -> int:
    index = _load_skill_disclosure(config_path, user_id).prepare_skill_index()
    for entry in index.entries:
        print(
            f"{entry.reference.name}\t{entry.reference.skill_type}"
            f"\tagent_created={str(entry.agent_created).lower()}"
            f"\tagent_can_update={str(entry.agent_can_update).lower()}"
            f"\tfreshness={entry.freshness:.2f}"
            f"\tfunction_group={entry.function_group}"
            f"\tprovides={','.join(entry.provides)}"
            f"\trequires={','.join(entry.requires)}"
            f"\t{entry.description}"
        )
    return 0


def _print_skill_index(config_path: Path, user_id: str) -> int:
    index = _load_skill_disclosure(config_path, user_id).prepare_skill_index()
    print(json.dumps(skill_index_to_dict(index), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _propose_skill_change(args: argparse.Namespace) -> int:
    updater = load_agent(args.common_config).for_user(args.user_id).skills.create_skill_updater()
    change = updater.propose_skill_change(
        args.name,
        args.goal,
        skill_type=args.skill_type,
    )
    print(f"Proposed Skill change: {change.change_id}")
    return 0


def _test_skill_change(args: argparse.Namespace) -> int:
    updater = load_agent(args.common_config).for_user(args.user_id).skills.create_skill_updater()
    report = updater.test_skill_change(args.change_id, _read_change_cases(Path(args.cases)))
    state = "passed" if report.passed else "failed"
    print(f"Skill change test {report.report_id}: {state} score={report.score:.4f}")
    return 0


def _apply_skill_change(args: argparse.Namespace) -> int:
    updater = load_agent(args.common_config).for_user(args.user_id).skills.create_skill_updater()
    manifest = updater.apply_skill_change(args.change_id)
    print(f"Applied Skill change: {manifest.skill_type}:{manifest.name}@{manifest.version}")
    return 0


def _undo_skill_change(args: argparse.Namespace) -> int:
    updater = load_agent(args.common_config).for_user(args.user_id).skills.create_skill_updater()
    manifest = updater.undo_skill_change(args.change_id)
    restored = "removed" if manifest is None else f"restored {manifest.skill_type}:{manifest.name}@{manifest.version}"
    print(f"Undid Skill change: {args.change_id} ({restored})")
    return 0


def _list_skill_changes(args: argparse.Namespace) -> int:
    updater = load_agent(args.common_config).for_user(args.user_id).skills.create_skill_updater()
    for change in updater.list_skill_changes():
        print(f"{change.change_id}\t{change.key}\t{change.goal}")
    return 0


def _show_skill_freshness(config_path: Path, user_id: str) -> int:
    config = load_common_config(config_path)
    store = load_event_store(config, user_id)
    rules = load_configured_freshness_rules(config, store=store)
    stats = calculate_skill_freshness(
        read_evaluation_records(store, source_type="agent_run"),
        rules,
    )
    if not stats:
        print("No skill freshness stats yet.")
        return 0
    for name, item in sorted(stats.items()):
        print(
            f"{name}\tfreshness={float(item['freshness']):.2f}"
            f"\tcalls={int(item['call_count'])}"
            f"\tgroup={item['function_group']}"
            f"\tsuccess={int(item['success_count'])}"
            f"\treplacements={int(item['same_function_successful_followups'])}"
        )
    return 0


def _validate_skills(config_path: Path, user_id: str) -> int:
    disclosure = _load_skill_disclosure(config_path, user_id)
    issues = disclosure.validate_skill_sources()
    if issues:
        for issue in issues:
            print(f"{issue.path}: {issue.message}")
        return 1
    print(f"{len(disclosure.prepare_skill_index().entries)} valid skills")
    return 0


def _show_skill_graph(args: argparse.Namespace) -> int:
    manifests = _resolve_skills(Path(args.common_config), args.user_id, args.name)
    for manifest in manifests:
        print(
            f"{manifest.name}\tprovides={','.join(manifest.provides)}"
            f"\trequires={','.join(manifest.requires)}"
        )
    return 0


def _write_skill_lock(args: argparse.Namespace) -> int:
    disclosure = _load_skill_disclosure(Path(args.common_config), args.user_id)
    index = disclosure.prepare_skill_index()
    entries = index.resolve_skill_dependencies(args.name)
    manifests = [
        disclosure.open_skill(
            entry.reference.name,
            entry.reference.skill_type,
        ).read_manifest()
        for entry in entries
    ]
    output = Path(args.output)
    write_skill_lock_file(manifests, output)
    print(f"Wrote skill lock: {output}")
    return 0


def _pack_skill(args: argparse.Namespace) -> int:
    package_path = _load_package_manager(Path(args.common_config), args.user_id).pack_skill(
        args.name,
        Path(args.output),
    )
    print(f"Packed skill: {package_path}")
    return 0


def _install_skill(args: argparse.Namespace) -> int:
    manifest = _load_package_manager(Path(args.common_config), args.user_id).install_skill(
        args.source,
        expected_sha256=args.expected_sha256,
    )
    print(f"Installed skill: {manifest.name}@{manifest.version}")
    return 0


def _update_skill(args: argparse.Namespace) -> int:
    manifest = _load_package_manager(Path(args.common_config), args.user_id).update_skill(
        args.name,
        args.source,
        expected_sha256=args.expected_sha256,
    )
    print(f"Updated skill: {manifest.name}@{manifest.version}")
    return 0


def _remove_skill(args: argparse.Namespace) -> int:
    manager = _load_package_manager(Path(args.common_config), args.user_id)
    manager.remove_skill(args.name)
    print(f"Removed skill: {args.name}")
    return 0


def _resolve_skills(
    config_path: Path,
    user_id: str,
    names: list[str],
) -> list[SkillManifest]:
    disclosure = _load_skill_disclosure(config_path, user_id)
    index = disclosure.prepare_skill_index()
    return [
        disclosure.open_skill(
            entry.reference.name,
            entry.reference.skill_type,
        ).read_manifest()
        for entry in index.resolve_skill_dependencies(names)
    ]


def _load_skill_disclosure(
    config_path: Path,
    user_id: str,
) -> ProgressiveDisclosureCore:
    config = load_common_config(config_path)
    return create_progressive_skill_disclosure(
        config,
        store=load_event_store(config, user_id),
    )


def _load_package_manager(config_path: Path, user_id: str) -> SkillPackageManager:
    config = load_common_config(config_path)
    store = load_event_store(config, user_id)
    return SkillPackageManager(
        create_progressive_skill_disclosure(
            config,
            store=store,
        ),
        store,
        ActionRules(),
    )


def _add_change_name_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--common-config", default="common.toml")
    parser.add_argument("--name", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--type", dest="skill_type")


def _add_change_id_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--common-config", default="common.toml")
    parser.add_argument("--change-id", required=True)


def _add_composition_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--common-config", default="common.toml")
    parser.add_argument("--name", action="append", required=True)


def _add_package_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--common-config", default="common.toml")
    parser.add_argument("--source", required=True)
    parser.add_argument("--expected-sha256", default="")


def _add_model_read_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--common-config")
    parser.add_argument("--user-id", default=LOCAL_USER_ID)
    parser.add_argument("--output", choices=["text", "json"], default="text")


def _add_model_write_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--common-config", required=True)
    parser.add_argument("--user-id", default=LOCAL_USER_ID)
    parser.add_argument("--output", choices=["text", "json"], default="text")


def _read_change_cases(path: Path) -> list[SkillChangeCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("evaluation cases file must contain a JSON array")
    cases: list[SkillChangeCase] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("each evaluation case must be a JSON object")
        allowed = {
            "name",
            "prompt",
            "expected_output_contains",
            "forbidden_output_contains",
            "expected_configuration",
        }
        if set(item) - allowed:
            raise ValueError("unknown evaluation case fields: " + ", ".join(sorted(set(item) - allowed)))
        expected_configuration = item.get("expected_configuration", {})
        if not isinstance(expected_configuration, dict):
            raise ValueError("evaluation case expected_configuration must be an object")
        cases.append(
            SkillChangeCase(
                name=_read_json_string(item, "name", required=True),
                prompt=_read_json_string(item, "prompt", required=True),
                expected_output_contains=_read_string_list(item, "expected_output_contains"),
                forbidden_output_contains=_read_string_list(item, "forbidden_output_contains"),
                expected_configuration=dict(expected_configuration),
            )
        )
    return cases


def _read_string_list(data: dict[str, object], name: str) -> list[str]:
    value = data.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"evaluation case {name} must be a string array")
    return list(value)


def _read_json_string(data: dict[str, object], name: str, *, required: bool = False) -> str:
    value = data.get(name)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        requirement = "a non-empty string" if required else "a string"
        raise ValueError(f"evaluation case {name} must be {requirement}")
    return value
