"""CLI command groups and their shared readers."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
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
from core.checks import ActionDecision, ActionDecisionType, ActionEffect, ActionRequest, ActionRules
from core.models import LOCAL_USER_ID
from core.state.events import EventStore
from core.skill_use.builtins import TaskSkillHandler
from core.skill_use.handlers import (
    SkillAction,
    SkillContext,
    SkillTool,
    read_optional_tool_string,
    read_optional_positive_tool_integer,
    read_required_tool_string,
)
from core.files import write_bytes_atomically


CommonConfigSource = CommonConfig | str | Path | None
WORKSPACE_FILE_LIMIT = 1_000_000
WORKSPACE_SEARCH_LIMIT = 200
WORKSPACE_COMMAND_TIMEOUT = 60


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
        if output not in {"text", "json"}:
            raise ValueError("CLI run output must be text or json")
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


class TerminalActionRules(ActionRules):
    """Ask in the terminal before a Runtime action leaves read-only state."""

    def check_action(self, request: ActionRequest) -> ActionDecision:
        decision = super().check_action(request)
        if decision.decision != ActionDecisionType.REQUIRE_CONFIRMATION:
            return decision
        effects = ", ".join(effect.value for effect in request.effects)
        print(f"Allow {effects} on {request.resource}? [y/N]", file=sys.stderr, end=" ", flush=True)
        try:
            answer = sys.stdin.readline().strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer in {"y", "yes"}:
            return ActionDecision(ActionDecisionType.ALLOW, "terminal user confirmed the action", True)
        return decision


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
        content = {"type": "string", "description": "Complete replacement text."}
        return (
            SkillTool("read_workspace_file", "Read one UTF-8 workspace file.", {"path": path}, self.read_file, SkillAction((ActionEffect.READ,), "workspace:file", "path"), ("path",)),
            SkillTool("search_workspace", "Search UTF-8 workspace files.", {"query": {"type": "string"}, "path": path}, self.search, SkillAction((ActionEffect.READ,), "workspace:search", "path"), ("query",)),
            SkillTool("write_workspace_file", "Create or replace one workspace file after confirmation.", {"path": path, "content": content}, self.write_file, SkillAction((ActionEffect.CREATE, ActionEffect.UPDATE), "workspace:file", "path"), ("path", "content")),
            SkillTool("patch_workspace_file", "Replace one exact text occurrence after confirmation.", {"path": path, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, self.patch_file, SkillAction((ActionEffect.UPDATE,), "workspace:file", "path"), ("path", "old_text", "new_text")),
            SkillTool("delete_workspace_file", "Delete one workspace file after confirmation.", {"path": path}, self.delete_file, SkillAction((ActionEffect.DELETE,), "workspace:file", "path"), ("path",)),
            SkillTool("run_workspace_check", "Run one declared verification command after confirmation.", {"command_number": {"type": "integer", "minimum": 1}}, self.run_check, SkillAction((ActionEffect.EXECUTE,), "workspace:command", "command_number"), ("command_number",)),
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

    def write_file(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_setting("write")
        selected = self._resolve(read_required_tool_string(arguments, "path"), allow_symlink=False)
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("tool argument 'content' must be a string")
        if selected.exists() and not selected.is_file():
            raise ValueError(f"workspace path is not a file: {selected}")
        if not selected.parent.is_dir():
            raise FileNotFoundError(f"workspace parent directory not found: {selected.parent}")
        existed = selected.exists()
        write_bytes_atomically(selected, content.encode("utf-8"))
        return {"path": self._relative(selected), "created": not existed, "updated": existed}

    def patch_file(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_setting("write")
        selected = self._resolve(read_required_tool_string(arguments, "path"), allow_symlink=False)
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        if not isinstance(old_text, str) or not isinstance(new_text, str) or not old_text:
            raise ValueError("old_text must be a non-empty string and new_text must be a string")
        current = self._read_text(selected)
        if current.count(old_text) != 1:
            raise ValueError("patch must match exactly one text occurrence")
        write_bytes_atomically(selected, current.replace(old_text, new_text).encode("utf-8"))
        return {"path": self._relative(selected), "updated": True}

    def delete_file(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_setting("write")
        selected = self._resolve(read_required_tool_string(arguments, "path"), allow_symlink=False)
        if not selected.is_file():
            raise FileNotFoundError(f"workspace file not found: {selected}")
        selected.unlink()
        return {"path": self._relative(selected), "deleted": True}

    def run_check(self, arguments: dict[str, object]) -> dict[str, object]:
        self._require_setting("execute")
        number = read_optional_positive_tool_integer(arguments, "command_number")
        commands = self.settings.verification_commands
        if number is None or number > len(commands):
            raise ValueError(f"verification command number must be between 1 and {len(commands)}")
        command = commands[number - 1]
        completed = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=WORKSPACE_COMMAND_TIMEOUT, check=False)
        return {"command_number": number, "command": command, "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}

    def _resolve(self, value: str, *, allow_symlink: bool = True) -> Path:
        if Path(value).is_absolute():
            raise PermissionError("workspace paths must be relative")
        if not allow_symlink and (self.root / value).is_symlink():
            raise PermissionError("workspace changes cannot follow symbolic links")
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

    def _require_setting(self, name: str) -> None:
        value = getattr(self.settings, name)
        if value == "deny":
            raise PermissionError(f"code configuration denies workspace {name}")


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
    return Agent(load_common_config(source), use_storage=use_storage, action_rules=TerminalActionRules())


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
