from __future__ import annotations

import argparse
import json
from pathlib import Path

from core import Agent, AgentConfig, create_skill_loader_for_agent_config
from skill import (
    EvaluationCase,
    SkillDependencyResolver,
    SkillFreshnessStore,
    SkillLoader,
    SkillManifest,
    explain_skill_selection,
    validate_skill_manifests,
)


def configure_skills_parser(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="skill_command")
    list_parser = subparsers.add_parser("list", help="list available skills")
    list_parser.add_argument("--config", default="agent.toml")
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


def run_skills_command(args: argparse.Namespace) -> int:
    handlers = {
        "list": lambda: _list_skills(Path(args.config)),
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
    }
    handler = handlers.get(args.skill_command)
    if handler is None:
        raise ValueError("skills command is required")
    return handler()


def _list_skills(config_path: Path) -> int:
    config = AgentConfig.load_from_file(config_path)
    for manifest in create_skill_loader_for_agent_config(config).list_skill_manifests():
        print(
            f"{manifest.name}\t{manifest.kind}"
            f"\tagent_created={str(manifest.agent_created).lower()}"
            f"\tagent_can_update={str(manifest.agent_can_update).lower()}"
            f"\tfreshness={manifest.freshness:.2f}"
            f"\tfunction_group={manifest.function_group}"
            f"\tprovides={','.join(manifest.provides)}"
            f"\trequires={','.join(manifest.requires)}"
            f"\t{manifest.description}"
        )
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
    loader = _load_skill_loader(config_path)
    issues = validate_skill_manifests(loader)
    if issues:
        for issue in issues:
            print(f"{issue.path}: {issue.message}")
        return 1
    print(f"{len(loader.list_skill_manifests())} valid skills")
    return 0


def _explain_skills(config_path: Path, prompt: str) -> int:
    config = AgentConfig.load_from_file(config_path)
    loader = create_skill_loader_for_agent_config(config)
    for selection in explain_skill_selection(loader, prompt, config.agent.skills):
        state = "selected" if selection.selected else "skipped"
        print(f"{selection.name}\t{state}\t{selection.reason}")
    return 0


def _show_skill_graph(args: argparse.Namespace) -> int:
    manifests = _resolve_skills(Path(args.config), args.name)
    for manifest in manifests:
        print(
            f"{manifest.name}\tprovides={','.join(manifest.provides)}"
            f"\trequires={','.join(manifest.requires)}"
        )
    return 0


def _write_skill_lock(args: argparse.Namespace) -> int:
    resolver = SkillDependencyResolver(_load_skill_loader(Path(args.config)))
    manifests = resolver.resolve_skills(args.name)
    output = Path(args.output)
    resolver.write_skill_lock(manifests, output)
    print(f"Wrote skill lock: {output}")
    return 0


def _resolve_skills(config_path: Path, names: list[str]) -> list[SkillManifest]:
    resolver = SkillDependencyResolver(_load_skill_loader(config_path))
    return resolver.resolve_skills(names)


def _load_skill_loader(config_path: Path) -> SkillLoader:
    config = AgentConfig.load_from_file(config_path)
    return create_skill_loader_for_agent_config(config)


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
