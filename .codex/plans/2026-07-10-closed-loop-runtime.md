# Closed-Loop Skill Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Super Agent from a unified skill manifest prototype into an observable runtime that can execute, evaluate, evolve, compose, and distribute skills.

**Architecture:** Keep a small mechanism-only kernel for transports, run events, loading, and atomic state changes. Keep prompts, routing, workflows, memory behavior, evaluation, and delegation as explicit skill kinds that share the `discover -> disclose -> run -> observe -> evolve` lifecycle.

**Tech Stack:** Python 3.11+ standard library, TOML, JSON/JSONL, MCP JSON-RPC over stdio, Swift 5.9/SwiftUI.

## Global Constraints

- Keep all release versions in the `0.0.x` series.
- Produce exactly one local commit for every completed version.
- Keep `Agent.add_subagent` and other public names explicit and readable.
- Do not add runtime Python dependencies.
- Preserve the user's unstaged `.gitignore` change and unrelated `eval/` data.
- Write behavior tests before production code and verify every test fails for the intended missing behavior.

---

### Task 1: v0.0.9 Run Contract and Trace

**Files:**
- Create: `src/core/run.py`
- Create: `src/super_agent.py`
- Modify: `src/core/agent.py`, `src/core/__init__.py`, `src/skill/manifest.py`, `src/skill/loader.py`, `src/cli.py`, `pyproject.toml`, `README.md`
- Reorganize/Test: `tests/agent/`, `tests/skills/`, `tests/commands/`

**Interfaces:**

```python
@dataclass(frozen=True)
class RunEvent:
    schema_version: int
    run_id: str
    sequence: int
    event_type: str
    created_at: str
    agent_name: str
    parent_run_id: str | None
    data: dict[str, object]

class RunContext:
    def record_event(self, event_type: str, data: dict[str, object] | None = None) -> RunEvent: ...

class RunTraceStore:
    def start_run(self, agent_name: str, prompt: str, parent_run_id: str | None = None) -> RunContext: ...
    def read_run_events(self, run_id: str) -> list[RunEvent]: ...
```

- [x] Move existing tests into responsibility-based packages and verify the unchanged suite passes.
- [x] Add failing tests for ordered trace events, parent run IDs, manifest schema version, validation, selection explanations, CLI JSON trace output, and the `super_agent` public facade.
- [x] Implement `RunContext`, `RunTraceStore`, schema validation, explicit CLI commands, and facade exports.
- [x] Run all Python tests, build a wheel, inspect its module list, update version to `0.0.9`, and commit `feat(runtime): 发布 v0.0.9 运行追踪契约`.

### Task 2: v0.0.10 Real Skill Execution

**Files:**
- Create: `src/core/tools.py`
- Modify: `src/core/provider.py`, `src/core/agent.py`, `src/core/run.py`, `src/skill/disclosure.py`, `src/skill/kinds/mcp.py`, `src/skill/kinds/workflow.py`, `src/skill/loader.py`, `src/cli.py`, `README.md`
- Test: `tests/agent/test_tools.py`, `tests/skills/test_mcp.py`, `tests/skills/test_workflow.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]

@dataclass(frozen=True)
class ModelResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str

class SkillTools:
    def list_skills(self) -> dict[str, object]: ...
    def read_skill(self, name: str) -> dict[str, object]: ...
    def list_skill_tools(self, name: str) -> dict[str, object]: ...
    def run_skill(self, name: str, tool: str, arguments: dict[str, object]) -> dict[str, object]: ...
```

- [x] Add failing tests for model-selected disclosure, multi-step React/loop execution, maximum-step termination, MCP initialize/tools-list/tools-call over a real fake stdio process, and tool trace events.
- [x] Implement provider tool-call parsing, clear built-in skill tools, MCP stdio JSON-RPC, and workflow stop conditions from TOML.
- [x] Verify direct mode remains one-shot while React/loop run until model completion or configured `max_steps`.
- [x] Update version to `0.0.10` and commit `feat(runtime): 发布 v0.0.10 真实 Skill 执行`.

### Task 3: v0.0.11 Unified CLI and macOS Runtime

**Files:**
- Modify: `src/cli.py`, `src/core/agent.py`, `src/core/run.py`, `src/skill/kinds/workflow.py`
- Rename: `src/frontend/mac/Sources/SuperAgentMac/Support/ModelChatClient.swift` to `AgentRuntimeClient.swift`
- Modify: `src/frontend/mac/Sources/SuperAgentMac/ChatStore.swift`, model/run DTOs, README files
- Test: `tests/commands/test_cli.py`, `tests/agent/test_agent.py`

**Interfaces:**

```json
{"prompt":"hello","messages":[{"role":"user","content":"hello"}]}
{"type":"event","event":{"run_id":"...","event_type":"run.started"}}
{"type":"result","result":{"text":"...","run_id":"...","subagent_results":[]}}
```

- [x] Add failing tests for JSON request input, JSONL event/result output, conversation messages, and serialized nested subagent results.
- [x] Implement the CLI runtime protocol and make Swift invoke `super-agent run --request-stdin --output jsonl`.
- [x] Remove duplicate provider HTTP behavior from Swift and construct the displayed run tree from runtime results.
- [x] Run Python tests and Swift build, update version to `0.0.11`, and commit `feat(frontend): 发布 v0.0.11 统一运行时`.

### Task 4: v0.0.12 Evaluated Skill Evolution

**Files:**
- Create: `src/skill/evolution/` with `candidate.py`, `evaluation.py`, `manager.py`, `freshness.py`
- Move/Delete: `src/skill/self_update.py`, `src/skill/freshness.py`
- Modify: `src/core/agent.py`, `src/skill/__init__.py`, `src/cli.py`, `README.md`
- Test: `tests/skills/test_evolution.py`, migrated freshness tests

**Interfaces:**

```python
class SkillEvolutionManager:
    def create_skill_candidate(self, name: str, goal: str) -> SkillCandidate: ...
    def evaluate_skill_candidate(self, candidate_id: str, cases: list[EvaluationCase]) -> EvaluationReport: ...
    def promote_skill_candidate(self, candidate_id: str) -> SkillManifest: ...
    def rollback_skill(self, name: str) -> SkillManifest: ...
    def evolve_skill(self, name: str, goal: str, cases: list[EvaluationCase]) -> EvolutionResult: ...
```

- [x] Add failing tests proving active files remain unchanged before promotion, locked skills cannot evolve, weak candidates are rejected, strong candidates are promoted, history is immutable, and rollback restores the previous version.
- [x] Implement deterministic case evaluation with optional evaluator instructions, candidate lineage, atomic promotion, and rollback.
- [x] Replace direct optimization commands with `propose`, `evaluate`, `promote`, `evolve`, and `rollback` commands.
- [x] Update version to `0.0.12` and commit `feat(evolution): 发布 v0.0.12 评价进化闭环`.

### Task 5: v0.0.13 Memory Operations

**Files:**
- Modify: `src/skill/kinds/memory.py`, `src/core/tools.py`, `src/core/agent.py`, `src/cli.py`, `README.md`
- Test: `tests/skills/test_memory.py`, `tests/agent/test_tools.py`

**Interfaces:**

```python
class MiniMemory:
    def add_memory_item(self, text: str, scope: str = "agent", source_run_id: str = "") -> MemoryItem: ...
    def recall_memory(self, query: str, scope: str = "agent", limit: int | None = None) -> list[MemoryItem]: ...
    def forget_memory(self, item_id: str) -> None: ...
    def consolidate_memory(self) -> list[MemoryItem]: ...
```

- [ ] Add failing tests for scoped recall, lexical ranking, event-sourced writes, forgetting, deterministic consolidation, and model-invoked memory tools.
- [ ] Implement memory policy loading from `[memory]`, keep policy separate from data, and expose explicit memory tools to workflows.
- [ ] Add CLI `memory list/add/recall/forget/consolidate` commands.
- [ ] Update version to `0.0.13` and commit `feat(memory): 发布 v0.0.13 记忆操作闭环`.

### Task 6: v0.0.14 Skill Composition and Delegation

**Files:**
- Create: `src/skill/ecosystem/resolver.py`, `src/skill/ecosystem/lock.py`
- Modify: `src/skill/manifest.py`, `src/skill/loader.py`, `src/core/tools.py`, `src/core/agent.py`, `src/cli.py`, `README.md`
- Test: `tests/skills/test_composition.py`, `tests/agent/test_subagents.py`

**Interfaces:**

```python
class SkillDependencyResolver:
    def resolve_skills(self, names: list[str]) -> list[SkillManifest]: ...
    def write_skill_lock(self, manifests: list[SkillManifest], path: Path) -> None: ...

class SkillTools:
    def list_subagents(self) -> dict[str, object]: ...
    def run_subagent(self, name: str, prompt: str) -> dict[str, object]: ...
```

- [ ] Add failing tests for `provides`, `requires`, missing dependencies, cycles, deterministic locks, model-selected delegation, and child trace linkage.
- [ ] Implement capability resolution and expose code-mounted subagents through explicit workflow tools while keeping `Agent.add_subagent` unchanged.
- [ ] Add CLI `skills graph` and `skills lock` commands.
- [ ] Update version to `0.0.14` and commit `feat(skill): 发布 v0.0.14 组合与委派`.

### Task 7: v0.0.15 Local and Git Skill Ecosystem

**Files:**
- Create: `src/skill/ecosystem/package.py`
- Modify: `src/skill/ecosystem/__init__.py`, `src/cli.py`, `README.md`
- Test: `tests/skills/test_packages.py`, `tests/commands/test_cli.py`

**Interfaces:**

```python
class SkillPackageManager:
    def pack_skill(self, name: str, output: Path) -> Path: ...
    def install_skill(self, source: str, expected_sha256: str = "") -> SkillManifest: ...
    def update_skill(self, name: str, source: str, expected_sha256: str = "") -> SkillManifest: ...
    def remove_skill(self, name: str) -> None: ...
```

- [ ] Add failing tests for deterministic ZIP output, local install, Git install through a local repository, hash rejection, update, remove, and path traversal rejection.
- [ ] Implement package operations with `zipfile`, `hashlib`, `tempfile`, `shutil`, and non-interactive `git` subprocesses.
- [ ] Add CLI `skills pack/install/update/remove` commands and document reproducible locks.
- [ ] Update version to `0.0.15` and commit `feat(ecosystem): 发布 v0.0.15 Skill 包管理`.

### Task 8: v0.0.16 Stable Experimental Contract and Benchmark

**Files:**
- Create: `src/core/benchmark.py`
- Modify: public exports, manifest/event serializers, `src/cli.py`, `README.md`, `examples/`
- Test: schema contract tests, benchmark tests, wheel import test

**Interfaces:**

```python
class SkillBenchmark:
    def run_cases(self, cases: list[BenchmarkCase]) -> BenchmarkReport: ...
```

- [ ] Add failing tests for strict schema v1, explicit migration errors, event schema stability, public facade imports, benchmark context savings, and JSON report output.
- [ ] Implement `super-agent benchmark`, freeze experimental schema v1, and document compatibility boundaries without claiming 1.0 stability.
- [ ] Run full Python tests, wheel build/install smoke test, Swift build, CLI smoke tests, and source scans.
- [ ] Update version to `0.0.16` and commit `feat(release): 发布 v0.0.16 闭环实验契约`.
