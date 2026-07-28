from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.agent import Agent
from capability.defaults import create_progressive_skill_disclosure
from capability.skill_executors import create_builtin_capabilities
from runtime.config import AgentConfig
from runtime.identity import LOCAL_USER_ID
from runtime.storage import create_storage_backend
from runtime.store import RuntimeStore
from runtime.safety import SafetyPolicy
from skill.disclosure import ProgressiveDisclosureCore, skill_index_to_dict
from skill.ecosystem.package import SkillPackageManager
from skill.evolution.evaluation import EvaluationCase
from skill.freshness import calculate_skill_freshness
from skill.ecosystem.lock import write_skill_lock_file
from skill.manifest import SkillManifest


def configure_skills_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="skill_command")
    list_parser = subparsers.add_parser("list", help="list available skills")
    list_parser.add_argument("--config", default="agent.toml")
    index_parser = subparsers.add_parser("index", help="print the central skill index as JSON")
    index_parser.add_argument("--config", default="agent.toml")
    index_parser.add_argument("--output", choices=["json"], default="json")
    propose_parser = subparsers.add_parser("propose", help="create an isolated skill candidate")
    _add_evolution_name_arguments(propose_parser)
    evaluate_parser = subparsers.add_parser("evaluate", help="evaluate a skill candidate")
    _add_evolution_candidate_arguments(evaluate_parser)
    evaluate_parser.add_argument("--cases", required=True)
    promote_parser = subparsers.add_parser("promote", help="promote a passing skill candidate")
    _add_evolution_candidate_arguments(promote_parser)
    evolve_parser = subparsers.add_parser("evolve", help="propose, evaluate, and promote a skill")
    _add_evolution_name_arguments(evolve_parser)
    evolve_parser.add_argument("--cases", required=True)
    rollback_parser = subparsers.add_parser("rollback", help="restore the previous skill revision")
    rollback_parser.add_argument("--config", default="agent.toml")
    rollback_parser.add_argument("--name", required=True)
    rollback_parser.add_argument("--capability")
    freshness_parser = subparsers.add_parser("freshness", help="show runtime skill freshness stats")
    freshness_parser.add_argument("--config", default="agent.toml")
    validate_parser = subparsers.add_parser("validate", help="validate every skill manifest")
    validate_parser.add_argument("--config", default="agent.toml")
    explain_parser = subparsers.add_parser("explain", help="explain skill selection for one prompt")
    explain_parser.add_argument("--config", default="agent.toml")
    explain_parser.add_argument("--prompt", required=True)
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
        evaluate_parser,
        promote_parser,
        evolve_parser,
        rollback_parser,
        freshness_parser,
        validate_parser,
        explain_parser,
        graph_parser,
        lock_parser,
        pack_parser,
        install_parser,
        update_parser,
        remove_parser,
    ):
        command_parser.add_argument("--user-id", default=LOCAL_USER_ID)


def run_skills_command(args: argparse.Namespace) -> int:
    handlers = {
        "list": lambda: _list_skills(Path(args.config), args.user_id),
        "index": lambda: _print_skill_index(Path(args.config), args.user_id),
        "propose": lambda: _propose_skill(args),
        "evaluate": lambda: _evaluate_skill(args),
        "promote": lambda: _promote_skill(args),
        "evolve": lambda: _evolve_skill(args),
        "rollback": lambda: _rollback_skill(args),
        "freshness": lambda: _show_skill_freshness(Path(args.config), args.user_id),
        "validate": lambda: _validate_skills(Path(args.config), args.user_id),
        "explain": lambda: _explain_skills(Path(args.config), args.user_id, args.prompt),
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
            f"{entry.reference.name}\t{entry.reference.capability}"
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


def _propose_skill(args: argparse.Namespace) -> int:
    manager = Agent(args.config).for_user(args.user_id).skills.create_evolution_manager()
    candidate = manager.create_skill_candidate(
        args.name,
        args.goal,
        capability=args.capability,
    )
    print(f"Proposed candidate: {candidate.candidate_id}")
    return 0


def _evaluate_skill(args: argparse.Namespace) -> int:
    manager = Agent(args.config).for_user(args.user_id).skills.create_evolution_manager()
    report = manager.evaluate_skill_candidate(args.candidate_id, _read_evaluation_cases(Path(args.cases)))
    state = "passed" if report.passed else "rejected"
    print(f"Evaluation {report.report_id}: {state} score={report.score:.4f}")
    return 0


def _promote_skill(args: argparse.Namespace) -> int:
    manager = Agent(args.config).for_user(args.user_id).skills.create_evolution_manager()
    manifest = manager.promote_skill_candidate(args.candidate_id)
    print(f"Promoted skill: {manifest.capability}:{manifest.name}@{manifest.version}")
    return 0


def _evolve_skill(args: argparse.Namespace) -> int:
    manager = Agent(args.config).for_user(args.user_id).skills.create_evolution_manager()
    result = manager.evolve_skill(
        args.name,
        args.goal,
        _read_evaluation_cases(Path(args.cases)),
        capability=args.capability,
    )
    print(f"Evolution {result.status}: {result.candidate.candidate_id} score={result.report.score:.4f}")
    return 0 if result.status == "promoted" else 1


def _rollback_skill(args: argparse.Namespace) -> int:
    manager = Agent(args.config).for_user(args.user_id).skills.create_evolution_manager()
    manifest = manager.rollback_skill(args.name, capability=args.capability)
    print(f"Rolled back skill: {manifest.capability}:{manifest.name}@{manifest.version}")
    return 0


def _show_skill_freshness(config_path: Path, user_id: str) -> int:
    config = AgentConfig.load_from_file(config_path)
    store = _load_runtime_store(config, user_id)
    stats = calculate_skill_freshness(
        store.read_evaluation_records(source_type="agent_run")
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


def _explain_skills(config_path: Path, user_id: str, prompt: str) -> int:
    config = AgentConfig.load_from_file(config_path)
    disclosure = create_progressive_skill_disclosure(
        config,
        store=_load_runtime_store(config, user_id),
    )
    disclosure.prepare_skill_index()
    decisions = disclosure.explain_skill_selection_for_prompt(
        prompt,
        config.agent.skills,
        allowed_capabilities=_default_model_context_capabilities(),
    )
    for decision in decisions:
        state = _selection_state(decision.selected, decision.reason)
        print(f"{decision.reference.name}\t{state}\t{decision.reason}")
    return 0


def _selection_state(selected: bool, reason: str) -> str:
    if reason == "not eligible for model context":
        return "not_applicable"
    return "selected" if selected else "skipped"


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
            entry.reference.capability,
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
            entry.reference.capability,
        ).read_manifest()
        for entry in index.resolve_skill_dependencies(names)
    ]


def _load_skill_disclosure(
    config_path: Path,
    user_id: str,
) -> ProgressiveDisclosureCore:
    config = AgentConfig.load_from_file(config_path)
    return create_progressive_skill_disclosure(
        config,
        store=_load_runtime_store(config, user_id),
    )


def _load_runtime_store(config: AgentConfig, user_id: str) -> RuntimeStore:
    backend = create_storage_backend(
        config.storage.backend,
        str(config.storage.path),
        config.storage.url_env,
    )
    return RuntimeStore(
        backend,
        config.storage.path,
        user_id,
        config.agent.name,
    )


def _load_package_manager(config_path: Path, user_id: str) -> SkillPackageManager:
    config = AgentConfig.load_from_file(config_path)
    if not config.paths.skills:
        raise ValueError("agent has no skill path configured")
    return SkillPackageManager(
        create_progressive_skill_disclosure(
            config,
            store=_load_runtime_store(config, user_id),
        ),
        config.paths.skills[0],
        SafetyPolicy.from_name(config.agent.safety),
    )


def _default_model_context_capabilities() -> set[str]:
    return {
        capability.capability_name
        for capability in create_builtin_capabilities()
        if capability.adds_model_context
    }


def _add_evolution_name_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--name", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--capability")


def _add_evolution_candidate_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--candidate-id", required=True)


def _add_composition_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--name", action="append", required=True)


def _add_package_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--source", required=True)
    parser.add_argument("--expected-sha256", default="")


def _read_evaluation_cases(path: Path) -> list[EvaluationCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("evaluation cases file must contain a JSON array")
    cases: list[EvaluationCase] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("each evaluation case must be a JSON object")
        cases.append(
            EvaluationCase(
                name=_read_json_string(item, "name", required=True),
                prompt=_read_json_string(item, "prompt", required=True),
                expected_output_contains=_read_string_list(item, "expected_output_contains"),
                forbidden_output_contains=_read_string_list(item, "forbidden_output_contains"),
                evaluator_instruction=_read_json_string(item, "evaluator_instruction"),
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
