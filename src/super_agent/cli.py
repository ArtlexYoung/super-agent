from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from super_agent import Agent, AgentConfig
from super_agent.memory import MiniMemory
from super_agent.skill import SkillLoader


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return _run_init_command(Path(args.path))
    if args.command == "run":
        return _run_prompt_command(Path(args.config), " ".join(args.prompt))
    if args.command == "skills" and args.skill_command == "list":
        return _run_skills_list_command(Path(args.config))
    if args.command == "memory" and args.memory_command == "habits":
        return _run_memory_habits_command(Path(args.config))
    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="super-agent")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="create a minimal agent project")
    init_parser.add_argument("--path", default=".", help="target directory")

    run_parser = subparsers.add_parser("run", help="run one prompt")
    run_parser.add_argument("prompt", nargs="+")
    run_parser.add_argument("--config", default="agent.toml")

    skills_parser = subparsers.add_parser("skills", help="manage skills")
    skill_subparsers = skills_parser.add_subparsers(dest="skill_command")
    list_parser = skill_subparsers.add_parser("list", help="list available skills")
    list_parser.add_argument("--config", default="agent.toml")

    memory_parser = subparsers.add_parser("memory", help="inspect memory")
    memory_subparsers = memory_parser.add_subparsers(dest="memory_command")
    habits_parser = memory_subparsers.add_parser("habits", help="show self-updated usage habits")
    habits_parser.add_argument("--config", default="agent.toml")
    return parser


def _run_init_command(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    skill_dir = root / "skills" / "echo"
    skill_dir.mkdir(parents=True, exist_ok=True)
    _write_file_if_missing(root / "agent.toml", _default_agent_config())
    _write_file_if_missing(skill_dir / "skill.toml", _default_skill_manifest())
    _write_file_if_missing(skill_dir / "SKILL.md", "Answer briefly and clearly.\n")
    print(f"Initialized super-agent project at {root}")
    return 0


def _run_prompt_command(config_path: Path, prompt: str) -> int:
    config = AgentConfig.load_from_file(config_path)
    result = Agent(config).run(prompt)
    for warning in result.warning_messages or []:
        print(f"Warning: {warning}")
    print(result.text)
    return 0


def _run_skills_list_command(config_path: Path) -> int:
    config = AgentConfig.load_from_file(config_path)
    for manifest in SkillLoader(config.paths.skills).list_skill_manifests():
        print(f"{manifest.name}\t{manifest.description}")
    return 0


def _run_memory_habits_command(config_path: Path) -> int:
    config = AgentConfig.load_from_file(config_path)
    instruction = MiniMemory(config.paths.memory).build_prompt_instruction()
    print(instruction or "No memory yet.")
    return 0


def _write_file_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _default_agent_config() -> str:
    return """
[agent]
name = "super-agent"
system = "You are a concise, helpful agent."
workflow = "direct"
skills = ["echo"]

[model]
provider = "mock"
model = "mock"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
""".lstrip()


def _default_skill_manifest() -> str:
    return """
name = "echo"
description = "Minimal example skill"
version = "0.1.0"
triggers = ["echo", "brief"]

[entry]
instructions = "SKILL.md"
""".lstrip()


if __name__ == "__main__":
    raise SystemExit(main())
