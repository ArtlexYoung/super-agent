from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from adapter.cli_support.cli_config import load_agent, load_common_config, load_event_store
from adapter.cli_support.cli_data import add_config_and_user_options, add_output_format_option, add_subcommand_parsers, print_cli_json, run_selected_cli_command
from core.checks import ActionRules
from core.models import LOCAL_USER_ID, read_object, read_text, read_text_list, reject_unknown_fields
from skill.discovery.catalog import ProgressiveDisclosureCore, skill_index_to_dict
from skill.handlers.runtime import create_progressive_skill_disclosure, create_skills, load_configured_freshness_rules
from skill.learning.freshness import calculate_skill_freshness
from skill.learning.records import read_evaluation_records
from skill.learning.update import SkillChangeCase
from skill.discovery.manifest import SkillManifest
from skill.handlers.package import SkillPackageManager, write_skill_lock_file
from skill.handlers.model_management import ModelSkillManager, model_skill_input_from_dict
from skill.handlers.models import ModelProfile, model_profile_to_dict, read_model_profiles, select_default_model_profile


def configure_skills_parser(parser: argparse.ArgumentParser) -> None:
    commands = add_subcommand_parsers(parser, "skill_command", (("list", "list available skills"), ("index", "print the central skill index as JSON"), ("freshness", "show runtime skill freshness stats"), ("validate", "validate every skill manifest"), ("graph", "resolve a skill dependency graph"), ("changes", "manage Skill changes"), ("packages", "manage Skill packages"), ("models", "manage model Skills")))
    commands["index"].add_argument("--output", choices=["json"], default="json")
    add_config_and_user_options(commands["graph"], config_default="common.toml")
    commands["graph"].add_argument("--name", action="append", required=True)
    configure_skill_changes_parser(commands["changes"])
    configure_skill_packages_parser(commands["packages"])
    configure_models_parser(commands["models"])
    for name in ("list", "index", "freshness", "validate"):
        add_config_and_user_options(commands[name], config_default="common.toml")


def configure_skill_changes_parser(parser: argparse.ArgumentParser) -> None:
    commands = add_subcommand_parsers(parser, "skill_change_command", (("propose", "propose an isolated Skill change"), ("test", "test a change without applying it"), ("apply", "apply a passing Skill change"), ("undo", "undo an applied Skill change"), ("list", "list proposed Skill changes")))
    add_config_and_user_options(commands["propose"], config_default="common.toml")
    commands["propose"].add_argument("--name", required=True)
    commands["propose"].add_argument("--goal", required=True)
    commands["propose"].add_argument("--type", dest="skill_type")
    for name in ("test", "apply", "undo"):
        add_config_and_user_options(commands[name], config_default="common.toml")
        commands[name].add_argument("--change-id", required=True)
    commands["test"].add_argument("--cases", required=True)
    add_config_and_user_options(commands["list"], config_default="common.toml")


def configure_skill_packages_parser(parser: argparse.ArgumentParser) -> None:
    commands = add_subcommand_parsers(parser, "skill_package_command", (("lock", "write a deterministic Skill lock"), ("pack", "pack one Skill as a deterministic ZIP"), ("install", "install a local, ZIP, or Git Skill"), ("update", "replace an installed Skill"), ("remove", "remove one installed Skill")))
    add_config_and_user_options(commands["lock"], config_default="common.toml")
    commands["lock"].add_argument("--name", action="append", required=True)
    commands["lock"].add_argument("--output", default="skill.lock")
    add_config_and_user_options(commands["pack"], config_default="common.toml")
    commands["pack"].add_argument("--name", required=True)
    commands["pack"].add_argument("--output", required=True)
    for name in ("install", "update"):
        add_config_and_user_options(commands[name], config_default="common.toml")
        commands[name].add_argument("--source", required=True)
        commands[name].add_argument("--expected-sha256", default="")
    commands["update"].add_argument("--name", required=True)
    add_config_and_user_options(commands["remove"], config_default="common.toml")
    commands["remove"].add_argument("--name", required=True)


def configure_models_parser(parser: argparse.ArgumentParser) -> None:
    commands = add_subcommand_parsers(parser, "models_command", (("list", "list model Skills or zero-configuration environment profiles"), ("resolve", "show the default model profile selected for this project"), ("save", "create or update one model Skill from JSON stdin"), ("remove", "remove one model Skill")))
    for name in ("list", "resolve"):
        add_config_and_user_options(commands[name])
        add_output_format_option(commands[name])
    for name in ("save", "remove"):
        add_config_and_user_options(commands[name], config_required=True)
        add_output_format_option(commands[name])
    commands["save"].add_argument("--request-stdin", action="store_true", required=True)
    commands["remove"].add_argument("--name", required=True)


def run_skills_command(args: argparse.Namespace) -> int:
    handlers = {"list": _list_skills, "index": _print_skill_index, "freshness": _show_skill_freshness, "validate": _validate_skills, "graph": _show_skill_graph, "changes": run_skill_changes_command, "packages": run_skill_packages_command, "models": run_models_command}
    return run_selected_cli_command(args, "skill_command", handlers, "skills command is required")


def run_skill_changes_command(args: argparse.Namespace) -> int:
    handlers = {name: _run_skill_change for name in ("propose", "test", "apply", "undo", "list")}
    return run_selected_cli_command(args, "skill_change_command", handlers, "skills changes command is required")


def run_skill_packages_command(args: argparse.Namespace) -> int:
    handlers = {"lock": _write_skill_lock, **{name: _run_skill_package for name in ("pack", "install", "update", "remove")}}
    return run_selected_cli_command(args, "skill_package_command", handlers, "skills packages command is required")


def run_models_command(args: argparse.Namespace) -> int:
    config = load_common_config(getattr(args, "common_config", None))
    output = getattr(args, "output", "text")
    command = args.models_command or "resolve"
    match command:
        case "list" | "resolve":
            store = load_event_store(config, getattr(args, "user_id", LOCAL_USER_ID))
            profiles = read_model_profiles(create_skills(config, store=store))
            if command == "list":
                if output == "json":
                    return print_cli_json({"schema_version": 2, "config_path": str(config.source), "models": [model_profile_to_dict(profile) for profile in profiles]})
                for profile in profiles:
                    _print_model_profile(profile)
            else:
                selected = select_default_model_profile(profiles)
                if output == "json":
                    return print_cli_json({"schema_version": 2, "config_path": str(config.source), "model": model_profile_to_dict(selected)})
                _print_model_profile(selected, prefix="selected")
                print(f"config\t{config.source}")
        case "save" | "remove":
            manager = ModelSkillManager(config, load_event_store(config, args.user_id), ActionRules())
            if command == "save":
                profile = manager.save_model_skill(model_skill_input_from_dict(json.loads(sys.stdin.read())))
                if output == "json":
                    return print_cli_json({"schema_version": 1, "action": "saved", "model": model_profile_to_dict(profile)})
                _print_model_profile(profile, prefix="saved")
            else:
                manager.remove_model_skill(args.name)
                if output == "json":
                    return print_cli_json({"schema_version": 1, "name": args.name, "removed": True})
                print(f"Removed model Skill: model:{args.name}")
        case _:
            raise ValueError(f"unknown models command: {command}")
    return 0


def _print_model_profile(profile: ModelProfile, prefix: str = "profile") -> None:
    data = model_profile_to_dict(profile)
    print(f"{prefix}\t{profile.key}\t{data['provider']}\t{profile.model}\tready={str(data['ready']).lower()}\tdefault={str(profile.default).lower()}\tsource={profile.source}\tbase_url={data['base_url'] or ''}\tapi_key_env={data['api_key_env'] or ''}")


def _list_skills(args: argparse.Namespace) -> int:
    index = _load_skill_disclosure(Path(args.common_config), args.user_id).prepare_skill_index()
    for entry in index.entries:
        print(f"{entry.reference.name}\t{entry.reference.skill_type}\tagent_created={str(entry.agent_created).lower()}\tagent_can_update={str(entry.agent_can_update).lower()}\tfreshness={entry.freshness:.2f}\tfunction_group={entry.function_group}\tprovides={','.join(entry.provides)}\trequires={','.join(entry.requires)}\t{entry.description}")
    return 0


def _print_skill_index(args: argparse.Namespace) -> int:
    index = _load_skill_disclosure(Path(args.common_config), args.user_id).prepare_skill_index()
    return print_cli_json(skill_index_to_dict(index))


def _run_skill_change(args: argparse.Namespace) -> int:
    updater = load_agent(args.common_config).for_user(args.user_id).skills.create_skill_updater()
    command = args.skill_change_command
    if command == "propose":
        change = updater.propose_skill_change(args.name, args.goal, skill_type=args.skill_type)
        print(f"Proposed Skill change: {change.change_id}")
    elif command == "test":
        report = updater.test_skill_change(args.change_id, _read_change_cases(Path(args.cases)))
        print(f"Skill change test {report.report_id}: {'passed' if report.passed else 'failed'} score={report.score:.4f}")
    elif command == "apply":
        manifest = updater.apply_skill_change(args.change_id)
        print(f"Applied Skill change: {manifest.skill_type}:{manifest.name}@{manifest.version}")
    elif command == "undo":
        manifest = updater.undo_skill_change(args.change_id)
        restored = "removed" if manifest is None else f"restored {manifest.skill_type}:{manifest.name}@{manifest.version}"
        print(f"Undid Skill change: {args.change_id} ({restored})")
    elif command == "list":
        for change in updater.list_skill_changes():
            print(f"{change.change_id}\t{change.key}\t{change.goal}")
    else:
        raise ValueError(f"unknown skills changes command: {command}")
    return 0


def _show_skill_freshness(args: argparse.Namespace) -> int:
    config = load_common_config(Path(args.common_config))
    store = load_event_store(config, args.user_id)
    rules = load_configured_freshness_rules(config, store=store)
    stats = calculate_skill_freshness(read_evaluation_records(store, source_type="agent_run"), rules)
    if not stats:
        print("No skill freshness stats yet.")
        return 0
    for name, item in sorted(stats.items()):
        print(f"{name}\tfreshness={float(item['freshness']):.2f}\tcalls={int(item['call_count'])}\tgroup={item['function_group']}\tsuccess={int(item['success_count'])}\treplacements={int(item['same_function_successful_followups'])}")
    return 0


def _validate_skills(args: argparse.Namespace) -> int:
    disclosure = _load_skill_disclosure(Path(args.common_config), args.user_id)
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
        print(f"{manifest.name}\tprovides={','.join(manifest.provides)}\trequires={','.join(manifest.requires)}")
    return 0


def _write_skill_lock(args: argparse.Namespace) -> int:
    disclosure = _load_skill_disclosure(Path(args.common_config), args.user_id)
    index = disclosure.prepare_skill_index()
    entries = index.resolve_skill_dependencies(args.name)
    manifests = [disclosure.open_skill(entry.reference.name, entry.reference.skill_type).read_manifest() for entry in entries]
    output = Path(args.output)
    write_skill_lock_file(manifests, output)
    print(f"Wrote skill lock: {output}")
    return 0


def _run_skill_package(args: argparse.Namespace) -> int:
    manager = _load_package_manager(Path(args.common_config), args.user_id)
    command = args.skill_package_command
    if command == "pack":
        output = manager.pack_skill(args.name, Path(args.output))
        print(f"Packed skill: {output}")
    elif command == "install":
        manifest = manager.install_skill(args.source, expected_sha256=args.expected_sha256)
        print(f"Installed skill: {manifest.name}@{manifest.version}")
    elif command == "update":
        manifest = manager.update_skill(args.name, args.source, expected_sha256=args.expected_sha256)
        print(f"Updated skill: {manifest.name}@{manifest.version}")
    elif command == "remove":
        manager.remove_skill(args.name)
        print(f"Removed skill: {args.name}")
    else:
        raise ValueError(f"unknown skills packages command: {command}")
    return 0


def _resolve_skills(config_path: Path, user_id: str, names: list[str]) -> list[SkillManifest]:
    disclosure = _load_skill_disclosure(config_path, user_id)
    index = disclosure.prepare_skill_index()
    return [disclosure.open_skill(entry.reference.name, entry.reference.skill_type).read_manifest() for entry in index.resolve_skill_dependencies(names)]


def _load_skill_disclosure(config_path: Path, user_id: str) -> ProgressiveDisclosureCore:
    config = load_common_config(config_path)
    return create_progressive_skill_disclosure(config, store=load_event_store(config, user_id))


def _load_package_manager(config_path: Path, user_id: str) -> SkillPackageManager:
    config = load_common_config(config_path)
    store = load_event_store(config, user_id)
    return SkillPackageManager(create_progressive_skill_disclosure(config, store=store), store, ActionRules())


def _read_change_cases(path: Path) -> list[SkillChangeCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("evaluation cases file must contain a JSON array")
    cases: list[SkillChangeCase] = []
    allowed = {"name", "prompt", "expected_output_contains", "forbidden_output_contains", "expected_configuration"}
    for position, item in enumerate(data):
        item = read_object(item, f"evaluation case {position}")
        reject_unknown_fields(item, allowed, "evaluation case fields")
        expected_configuration = dict(read_object(item.get("expected_configuration", {}), "evaluation case expected_configuration"))
        cases.append(SkillChangeCase(name=read_text(item.get("name"), "evaluation case name"), prompt=read_text(item.get("prompt"), "evaluation case prompt"), expected_output_contains=read_text_list(item.get("expected_output_contains", []), "evaluation case expected_output_contains"), forbidden_output_contains=read_text_list(item.get("forbidden_output_contains", []), "evaluation case forbidden_output_contains"), expected_configuration=expected_configuration))
    return cases
