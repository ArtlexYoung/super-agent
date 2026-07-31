from __future__ import annotations

import argparse
import json
from pathlib import Path

from adapter.cli_adapter import load_agent, load_agent_config, load_event_store
from core.skill_use.defaults import (
    create_progressive_skill_disclosure,
    load_configured_freshness_rules,
)
from core.config import AgentConfig
from core.models import LOCAL_USER_ID
from core.checks import ActionRules
from skill.disclosure import ProgressiveDisclosureCore, skill_index_to_dict
from core.skill_use.files.package import SkillPackageManager
from core.skill_use.update import SkillChangeCase
from core.evaluation.freshness import calculate_skill_freshness
from core.evaluation.records import read_evaluation_records
from core.skill_use.files.lock import write_skill_lock_file
from skill.manifest import SkillManifest


def configure_skills_parser(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction:
    subparsers = parser.add_subparsers(dest="skill_command")
    list_parser = subparsers.add_parser("list", help="list available skills")
    list_parser.add_argument("--config", default="agent.toml")
    index_parser = subparsers.add_parser("index", help="print the central skill index as JSON")
    index_parser.add_argument("--config", default="agent.toml")
    index_parser.add_argument("--output", choices=["json"], default="json")
    propose_parser = subparsers.add_parser("propose-change", help="propose an isolated Skill change")
    _add_change_name_arguments(propose_parser)
    test_parser = subparsers.add_parser("test-change", help="test a Skill change without applying it")
    _add_change_id_arguments(test_parser)
    test_parser.add_argument("--cases", required=True)
    apply_parser = subparsers.add_parser("apply-change", help="apply a passing Skill change")
    _add_change_id_arguments(apply_parser)
    undo_parser = subparsers.add_parser("undo-change", help="undo an applied Skill change")
    _add_change_id_arguments(undo_parser)
    changes_parser = subparsers.add_parser("list-changes", help="list proposed Skill changes")
    changes_parser.add_argument("--config", default="agent.toml")
    freshness_parser = subparsers.add_parser("freshness", help="show runtime skill freshness stats")
    freshness_parser.add_argument("--config", default="agent.toml")
    validate_parser = subparsers.add_parser("validate", help="validate every skill manifest")
    validate_parser.add_argument("--config", default="agent.toml")
    graph_parser = subparsers.add_parser("graph", help="resolve a skill dependency graph")
    _add_composition_arguments(graph_parser)
    lock_parser = subparsers.add_parser("lock", help="write a deterministic skill lock")
    _add_composition_arguments(lock_parser)
    lock_parser.add_argument("--output", default="skill.lock")
    pack_parser = subparsers.add_parser("pack", help="pack one skill as a deterministic ZIP")
    pack_parser.add_argument("--config", default="agent.toml")
    pack_parser.add_argument("--name", required=True)
    pack_parser.add_argument("--output", required=True)
    install_parser = subparsers.add_parser("install", help="install a local, ZIP, or Git skill")
    _add_package_source_arguments(install_parser)
    update_parser = subparsers.add_parser("update", help="replace an installed skill from a source")
    _add_package_source_arguments(update_parser)
    update_parser.add_argument("--name", required=True)
    remove_parser = subparsers.add_parser("remove", help="remove one installed skill")
    remove_parser.add_argument("--config", default="agent.toml")
    remove_parser.add_argument("--name", required=True)
    for command_parser in (
        list_parser,
        index_parser,
        propose_parser,
        test_parser,
        apply_parser,
        undo_parser,
        changes_parser,
        freshness_parser,
        validate_parser,
        graph_parser,
        lock_parser,
        pack_parser,
        install_parser,
        update_parser,
        remove_parser,
    ):
        command_parser.add_argument("--user-id", default=LOCAL_USER_ID)
    return subparsers


def run_skills_command(args: argparse.Namespace) -> int:
    handlers = {
        "list": lambda: _list_skills(Path(args.config), args.user_id),
        "index": lambda: _print_skill_index(Path(args.config), args.user_id),
        "propose-change": lambda: _propose_skill_change(args),
        "test-change": lambda: _test_skill_change(args),
        "apply-change": lambda: _apply_skill_change(args),
        "undo-change": lambda: _undo_skill_change(args),
        "list-changes": lambda: _list_skill_changes(args),
        "freshness": lambda: _show_skill_freshness(Path(args.config), args.user_id),
        "validate": lambda: _validate_skills(Path(args.config), args.user_id),
        "graph": lambda: _show_skill_graph(args),
        "lock": lambda: _write_skill_lock(args),
        "pack": lambda: _pack_skill(args),
        "install": lambda: _install_skill(args),
        "update": lambda: _update_skill(args),
        "remove": lambda: _remove_skill(args),
    }
    handler = handlers.get(args.skill_command)
    if handler is None:
        raise ValueError("skills command is required")
    return handler()


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
    updater = load_agent(args.config).for_user(args.user_id).skills.create_skill_updater()
    change = updater.propose_skill_change(
        args.name,
        args.goal,
        skill_type=args.skill_type,
    )
    print(f"Proposed Skill change: {change.change_id}")
    return 0


def _test_skill_change(args: argparse.Namespace) -> int:
    updater = load_agent(args.config).for_user(args.user_id).skills.create_skill_updater()
    report = updater.test_skill_change(args.change_id, _read_change_cases(Path(args.cases)))
    state = "passed" if report.passed else "failed"
    print(f"Skill change test {report.report_id}: {state} score={report.score:.4f}")
    return 0


def _apply_skill_change(args: argparse.Namespace) -> int:
    updater = load_agent(args.config).for_user(args.user_id).skills.create_skill_updater()
    manifest = updater.apply_skill_change(args.change_id)
    print(f"Applied Skill change: {manifest.skill_type}:{manifest.name}@{manifest.version}")
    return 0


def _undo_skill_change(args: argparse.Namespace) -> int:
    updater = load_agent(args.config).for_user(args.user_id).skills.create_skill_updater()
    manifest = updater.undo_skill_change(args.change_id)
    restored = "removed" if manifest is None else f"restored {manifest.skill_type}:{manifest.name}@{manifest.version}"
    print(f"Undid Skill change: {args.change_id} ({restored})")
    return 0


def _list_skill_changes(args: argparse.Namespace) -> int:
    updater = load_agent(args.config).for_user(args.user_id).skills.create_skill_updater()
    for change in updater.list_skill_changes():
        print(f"{change.change_id}\t{change.key}\t{change.goal}")
    return 0


def _show_skill_freshness(config_path: Path, user_id: str) -> int:
    config = load_agent_config(config_path)
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
    manifests = _resolve_skills(Path(args.config), args.user_id, args.name)
    for manifest in manifests:
        print(
            f"{manifest.name}\tprovides={','.join(manifest.provides)}"
            f"\trequires={','.join(manifest.requires)}"
        )
    return 0


def _write_skill_lock(args: argparse.Namespace) -> int:
    disclosure = _load_skill_disclosure(Path(args.config), args.user_id)
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
    package_path = _load_package_manager(Path(args.config), args.user_id).pack_skill(
        args.name,
        Path(args.output),
    )
    print(f"Packed skill: {package_path}")
    return 0


def _install_skill(args: argparse.Namespace) -> int:
    manifest = _load_package_manager(Path(args.config), args.user_id).install_skill(
        args.source,
        expected_sha256=args.expected_sha256,
    )
    print(f"Installed skill: {manifest.name}@{manifest.version}")
    return 0


def _update_skill(args: argparse.Namespace) -> int:
    manifest = _load_package_manager(Path(args.config), args.user_id).update_skill(
        args.name,
        args.source,
        expected_sha256=args.expected_sha256,
    )
    print(f"Updated skill: {manifest.name}@{manifest.version}")
    return 0


def _remove_skill(args: argparse.Namespace) -> int:
    manager = _load_package_manager(Path(args.config), args.user_id)
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
    config = load_agent_config(config_path)
    return create_progressive_skill_disclosure(
        config,
        store=load_event_store(config, user_id),
    )


def _load_package_manager(config_path: Path, user_id: str) -> SkillPackageManager:
    config = load_agent_config(config_path)
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
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--name", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--type", dest="skill_type")


def _add_change_id_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--change-id", required=True)


def _add_composition_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--name", action="append", required=True)


def _add_package_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--source", required=True)
    parser.add_argument("--expected-sha256", default="")


def _read_change_cases(path: Path) -> list[SkillChangeCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("evaluation cases file must contain a JSON array")
    cases: list[SkillChangeCase] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("each evaluation case must be a JSON object")
        cases.append(
            SkillChangeCase(
                name=_read_json_string(item, "name", required=True),
                prompt=_read_json_string(item, "prompt", required=True),
                expected_output_contains=_read_string_list(item, "expected_output_contains"),
                forbidden_output_contains=_read_string_list(item, "forbidden_output_contains"),
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
