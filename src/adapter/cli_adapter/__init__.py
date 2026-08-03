"""CLI command groups and their shared readers."""

from __future__ import annotations

import argparse
import json
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

from super_agent import Agent
from core.config import (
    CodeConfig,
    CodeSettings,
    CommonConfig,
    find_optional_config_file,
    reject_unknown_settings,
    require_config_header,
)
from core.checks import ActionEffect
from core.models import LOCAL_USER_ID
from core.state.events import EventStore
from core.skill_use.builtins import TaskSkillHandler
from core.skill_use.handlers import (
    SkillAction,
    SkillContext,
    SkillTool,
    read_optional_tool_string,
    read_required_tool_string,
)


CommonConfigSource = CommonConfig | str | Path | None
WORKSPACE_FILE_LIMIT = 1_000_000
WORKSPACE_SEARCH_LIMIT = 200


@dataclass(frozen=True)
class CliConfig:
    """Terminal behavior that is independent from Agent and task configuration."""

    user_id: str
    output: str
    save: bool
    show_summary: bool
    source: Path

    @classmethod
    def load_from_file(cls, path: str | Path) -> "CliConfig":
        source = Path(path).expanduser().absolute()
        data = tomllib.loads(source.read_text(encoding="utf-8"))
        require_config_header(data, "cli")
        reject_unknown_settings(data, {"schema_version", "kind", "run"}, "CLI configuration tables")
        run = data.get("run", {})
        if not isinstance(run, dict):
            raise ValueError("CLI run settings must be a table")
        reject_unknown_settings(run, {"user_id", "output", "save", "show_summary"}, "CLI run settings")
        user_id = str(run.get("user_id", LOCAL_USER_ID)).strip()
        if not user_id:
            raise ValueError("CLI run user_id cannot be empty")
        output = str(run.get("output", "text")).strip().lower()
        if output not in {"text", "json", "jsonl"}:
            raise ValueError("CLI run output must be text, json, or jsonl")
        save = _read_cli_boolean(run.get("save", False), "save")
        summary = _read_cli_boolean(run.get("show_summary", True), "show_summary")
        return cls(user_id, output, save, summary, source)

    @classmethod
    def load_automatically(cls, base_directory: str | Path | None = None, environment: dict[str, str] | None = None) -> "CliConfig":
        base, source = find_optional_config_file(
            "cli.toml", "SUPER_AGENT_CLI_CONFIG", base_directory=base_directory, environment=environment,
        )
        return cls.load_from_file(source) if source else cls.create_default(base)

    @classmethod
    def create_default(cls, base_directory: str | Path | None = None) -> "CliConfig":
        base = Path.cwd() if base_directory is None else Path(base_directory).expanduser().absolute()
        return cls(LOCAL_USER_ID, "text", False, True, base / "cli.toml")


def load_common_config(source: CommonConfigSource = None) -> CommonConfig:
    if source is None:
        return CommonConfig.load_automatically()
    if isinstance(source, CommonConfig):
        return source
    return CommonConfig.load_from_file(source)


def load_cli_config(source: str | Path | None = None) -> CliConfig:
    if source is None:
        return CliConfig.load_automatically()
    return CliConfig.load_from_file(source)


def attach_code_config_to_agent(agent: Agent, source: str | Path | None = None) -> None:
    """Attach code settings without reading them until task:code is loaded."""

    def read_code_workspace(context: SkillContext) -> tuple[str, tuple[SkillTool, ...]]:
        if context.reference.name != "code":
            return "", ()
        config = CodeConfig.load_automatically() if source is None else CodeConfig.load_from_file(source)
        instructions = "# Coding workspace (does not grant file or process authority)\n" + json.dumps(asdict(config.settings), default=str)
        return instructions, CodeWorkspace(config.settings).list_tools()

    agent._add_skill_handler(TaskSkillHandler(read_code_workspace))


class CodeWorkspace:
    """Keep code-task file reads inside one validated workspace."""

    def __init__(self, settings: CodeSettings) -> None:
        self.settings = settings
        self.root = settings.root.resolve()
        self.ignored = tuple((self.root / item).resolve() for item in settings.ignored_paths)

    def list_tools(self) -> tuple[SkillTool, ...]:
        path = {"type": "string", "description": "Path relative to the configured workspace."}
        return (
            SkillTool("read_workspace_file", "Read one UTF-8 workspace file.", {"path": path}, self.read_file, SkillAction((ActionEffect.READ,), "workspace:file", "path"), ("path",)),
            SkillTool("search_workspace", "Search UTF-8 workspace files.", {"query": {"type": "string"}, "path": path}, self.search, SkillAction((ActionEffect.READ,), "workspace:search", "path"), ("query",)),
        )

    def read_file(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_read()
        selected = self._resolve(read_required_tool_string(arguments, "path"))
        if not selected.is_file():
            raise FileNotFoundError(f"workspace file not found: {selected}")
        return {"path": self._relative(selected), "content": self._read_text(selected)}

    def search(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_read()
        query = read_required_tool_string(arguments, "query")
        selected = self._resolve(read_optional_tool_string(arguments, "path") or ".")
        if not selected.exists():
            raise FileNotFoundError(f"workspace path not found: {selected}")
        candidates = [selected] if selected.is_file() else selected.rglob("*")
        matches, skipped = [], []
        for candidate in candidates:
            if not candidate.is_file() or self._is_ignored(candidate):
                continue
            if candidate.is_symlink():
                skipped.append({"path": self._relative(candidate), "error": "symbolic links are not searched"})
                continue
            try:
                content = self._read_text(candidate)
            except (OSError, ValueError) as error:
                skipped.append({"path": self._relative(candidate), "error": str(error)})
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if query in line:
                    matches.append({"path": self._relative(candidate), "line": number, "text": line})
                    if len(matches) > WORKSPACE_SEARCH_LIMIT:
                        raise ValueError("workspace search has more than 200 matches; narrow the query")
        return {"query": query, "matches": matches, "skipped": skipped}

    def _resolve(self, value: str) -> Path:
        if Path(value).is_absolute():
            raise PermissionError("workspace paths must be relative")
        selected = (self.root / value).resolve()
        if selected != self.root and self.root not in selected.parents:
            raise PermissionError(f"path is outside the workspace: {value}")
        if self._is_ignored(selected):
            raise PermissionError(f"path is ignored by code configuration: {value}")
        return selected

    def _is_ignored(self, path: Path) -> bool:
        selected = path.resolve()
        return any(selected == item or item in selected.parents for item in self.ignored)

    def _relative(self, path: Path) -> str:
        return path.absolute().relative_to(self.root).as_posix()

    def _read_text(self, path: Path) -> str:
        content = path.read_bytes()
        if len(content) > WORKSPACE_FILE_LIMIT:
            raise ValueError(f"workspace file exceeds {WORKSPACE_FILE_LIMIT} bytes: {path}")
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"workspace file is not UTF-8 text: {path}") from error

    def _require_read(self) -> None:
        if self.settings.read != "allow":
            raise PermissionError(f"code configuration sets reads to {self.settings.read}")


def configure_config_parser(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(cli_config=None)
    subparsers = parser.add_subparsers(dest="config_command")
    for name, help_text in (
        ("show", "show active CLI settings"),
        ("validate", "validate CLI settings"),
    ):
        subparsers.add_parser(name, help=help_text).add_argument("--cli-config")


def run_config_command(args: argparse.Namespace) -> int:
    config = load_cli_config(args.cli_config)
    if args.config_command == "validate":
        print(f"OK  CLI configuration: {_cli_source_label(config)}")
    else:
        _print_cli_config(config)
    return 0


def load_agent(source: CommonConfigSource = None, *, use_storage: bool = True) -> Agent:
    return Agent(load_common_config(source), use_storage=use_storage)


def load_event_store(source: CommonConfigSource = None, user_id: str = LOCAL_USER_ID) -> EventStore:
    config = load_common_config(source)
    from adapter.storage import create_storage_backend

    backend = create_storage_backend(
        config.storage.backend, str(config.storage.path), config.storage.url_env
    )
    return EventStore(backend, config.storage.path, user_id, config.agent.name)


def _print_cli_config(config: CliConfig) -> None:
    print(f"CLI configuration: {_cli_source_label(config)}")
    print(f"run.user_id = {config.user_id}")
    print(f"run.output = {config.output}")
    print(f"run.save = {str(config.save).lower()}")
    print(f"run.show_summary = {str(config.show_summary).lower()}")


def _cli_source_label(config: CliConfig) -> str:
    return str(config.source) if config.source.is_file() else "built-in defaults"


def _read_cli_boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"CLI run {name} must be true or false")
    return value
