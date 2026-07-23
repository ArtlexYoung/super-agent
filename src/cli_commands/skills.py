from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.agent import Agent
from capability.defaults import create_default_skill_retriever
from capability.skill_executors import create_builtin_skill_executors
from runtime.config import AgentConfig
from skill.disclosure import ProgressiveDisclosureCore, SkillIndexEntry, skill_index_to_dict
from skill.ecosystem.package import SkillPackageManager
from skill.evolution.evaluation import EvaluationCase
from skill.evolution.freshness import SkillFreshnessStore
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


def run_skills_command(args: argparse.Namespace) -> int:
    handlers = {
        "list": lambda: _list_skills(Path(args.config)),
        "index": lambda: _print_skill_index(Path(args.config)),
        "propose": lambda: _propose_skill(args),
        "evaluate": lambda: _evaluate_skill(args),
        "promote": lambda: _promote_skill(args),
        "evolve": lambda: _evolve_skill(args),
        "rollback": lambda: _rollback_skill(args),
        "freshness": lambda: _show_skill_freshness(Path(args.config)),
        "validate": lambda: _validate_skills(Path(args.config)),
        "explain": lambda: _explain_skills(Path(args.config), args.prompt),
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


def _list_skills(config_path: Path) -> int:
    index = _load_skill_disclosure(config_path).prepare_skill_index()
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


def _print_skill_index(config_path: Path) -> int:
    index = _load_skill_disclosure(config_path).prepare_skill_index()
    print(json.dumps(skill_index_to_dict(index), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _propose_skill(args: argparse.Namespace) -> int:
    manager = Agent.load_from_config_file(args.config).create_skill_evolution_manager()
    candidate = manager.create_skill_candidate(args.name, args.goal)
    print(f"Proposed candidate: {candidate.candidate_id}")
    return 0


def _evaluate_skill(args: argparse.Namespace) -> int:
    manager = Agent.load_from_config_file(args.config).create_skill_evolution_manager()
    report = manager.evaluate_skill_candidate(args.candidate_id, _read_evaluation_cases(Path(args.cases)))
    state = "passed" if report.passed else "rejected"
    print(f"Evaluation {report.report_id}: {state} score={report.score:.4f}")
    return 0


def _promote_skill(args: argparse.Namespace) -> int:
    manager = Agent.load_from_config_file(args.config).create_skill_evolution_manager()
    manifest = manager.promote_skill_candidate(args.candidate_id)
    print(f"Promoted skill: {manifest.name}@{manifest.version}")
    return 0


def _evolve_skill(args: argparse.Namespace) -> int:
    manager = Agent.load_from_config_file(args.config).create_skill_evolution_manager()
    result = manager.evolve_skill(args.name, args.goal, _read_evaluation_cases(Path(args.cases)))
    print(f"Evolution {result.status}: {result.candidate.candidate_id} score={result.report.score:.4f}")
    return 0 if result.status == "promoted" else 1


def _rollback_skill(args: argparse.Namespace) -> int:
    manager = Agent.load_from_config_file(args.config).create_skill_evolution_manager()
    manifest = manager.rollback_skill(args.name)
    print(f"Rolled back skill: {manifest.name}@{manifest.version}")
    return 0


def _show_skill_freshness(config_path: Path) -> int:
    config = AgentConfig.load_from_file(config_path)
    stats = SkillFreshnessStore(config.paths.memory).read_skill_stats()
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


def _validate_skills(config_path: Path) -> int:
    disclosure = _load_skill_disclosure(config_path)
    issues = disclosure.validate_skill_sources()
    if issues:
        for issue in issues:
            print(f"{issue.path}: {issue.message}")
        return 1
    print(f"{len(disclosure.prepare_skill_index().entries)} valid skills")
    return 0


def _explain_skills(config_path: Path, prompt: str) -> int:
    config = AgentConfig.load_from_file(config_path)
    disclosure = create_default_skill_retriever(config)
    index = disclosure.prepare_skill_index()
    selected = {
        reference.key
        for reference in disclosure.select_skill_references_for_prompt(
            prompt,
            config.agent.skills,
            allowed_capabilities=_default_model_context_capabilities(),
        )
    }
    for entry in index.entries:
        is_selected = entry.reference.key in selected
        state = "selected" if is_selected else "skipped"
        reason = _explain_index_entry(entry, prompt, config.agent.skills, is_selected)
        print(f"{entry.reference.name}\t{state}\t{reason}")
    return 0


def _explain_index_entry(
    entry: SkillIndexEntry,
    prompt: str,
    enabled_names: list[str],
    selected: bool,
) -> str:
    if entry.reference.capability not in _default_model_context_capabilities():
        return "runtime control skill"
    prompt_text = prompt.lower()
    trigger = next((value for value in entry.triggers if value and value in prompt_text), None)
    if trigger is not None:
        return f"matched trigger: {trigger}"
    if entry.reference.name in {name.lower() for name in enabled_names}:
        return "enabled by agent config"
    return "selected as dependency" if selected else "no trigger matched"


def _show_skill_graph(args: argparse.Namespace) -> int:
    manifests = _resolve_skills(Path(args.config), args.name)
    for manifest in manifests:
        print(
            f"{manifest.name}\tprovides={','.join(manifest.provides)}"
            f"\trequires={','.join(manifest.requires)}"
        )
    return 0


def _write_skill_lock(args: argparse.Namespace) -> int:
    disclosure = _load_skill_disclosure(Path(args.config))
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
    package_path = _load_package_manager(Path(args.config)).pack_skill(args.name, Path(args.output))
    print(f"Packed skill: {package_path}")
    return 0


def _install_skill(args: argparse.Namespace) -> int:
    manifest = _load_package_manager(Path(args.config)).install_skill(
        args.source,
        expected_sha256=args.expected_sha256,
    )
    print(f"Installed skill: {manifest.name}@{manifest.version}")
    return 0


def _update_skill(args: argparse.Namespace) -> int:
    manifest = _load_package_manager(Path(args.config)).update_skill(
        args.name,
        args.source,
        expected_sha256=args.expected_sha256,
    )
    print(f"Updated skill: {manifest.name}@{manifest.version}")
    return 0


def _remove_skill(args: argparse.Namespace) -> int:
    manager = _load_package_manager(Path(args.config))
    manager.remove_skill(args.name)
    print(f"Removed skill: {args.name}")
    return 0


def _resolve_skills(config_path: Path, names: list[str]) -> list[SkillManifest]:
    disclosure = _load_skill_disclosure(config_path)
    index = disclosure.prepare_skill_index()
    return [
        disclosure.open_skill(
            entry.reference.name,
            entry.reference.capability,
        ).read_manifest()
        for entry in index.resolve_skill_dependencies(names)
    ]


def _load_skill_disclosure(config_path: Path) -> ProgressiveDisclosureCore:
    config = AgentConfig.load_from_file(config_path)
    return create_default_skill_retriever(config)


def _load_package_manager(config_path: Path) -> SkillPackageManager:
    config = AgentConfig.load_from_file(config_path)
    if not config.paths.skills:
        raise ValueError("agent has no skill path configured")
    return SkillPackageManager(create_default_skill_retriever(config), config.paths.skills[0])


def _default_model_context_capabilities() -> set[str]:
    return {
        name
        for name, executor in create_builtin_skill_executors().items()
        if executor.adds_model_context
    }


def _add_evolution_name_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--name", required=True)
    parser.add_argument("--goal", required=True)


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
