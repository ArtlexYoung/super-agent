from pathlib import Path


def write_memory_skill(root: Path, name: str = "default") -> None:
    skill_dir = root / "skills" / "memory" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 1
name = "{name}"
kind = "memory"
description = "Default memory"
version = "0.1.0"
triggers = []

[memory]
""".strip(),
        encoding="utf-8",
    )


def write_workflow_skill(root: Path, name: str = "direct", mode: str = "direct") -> None:
    skill_dir = root / "skills" / "workflow" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.toml").write_text(
        f"""
schema_version = 1
name = "{name}"
kind = "workflow"
description = "{name} workflow"
version = "0.1.0"
triggers = []

[workflow]
mode = "{mode}"
""".strip(),
        encoding="utf-8",
    )
