# Eval Assets

All benchmark datasets, validation harnesses, run logs, and evaluation notes for
this project should live under `eval/`.

## Agent Benchmark Reports

- [`reports/token-usage-glm4-9b-20260806/`](reports/token-usage-glm4-9b-20260806/)
  compares Codex, Claude Code, Super Agent, and a direct Raw Model baseline on
  HumanEval+ and LiveCodeBench Codegen using `THUDM/GLM-4-9B-0414`. It includes
  tested versions, task-level input/output token counts, pass status, and
  aggregate efficiency statistics. The Raw Model baseline sends one OpenAI Chat
  Completions request per task without an agent runtime.
- `runner/` contains the isolated generation, scoring, and reporting commands.
- `runtime/proxy/` contains the loopback OpenAI/Anthropic protocol adapter used
  to collect provider-reported token usage without retaining prompts or model
  output.

## Layout

- `datasets/tau3-bench/`: Sierra `tau2-bench` main-branch snapshot. This is the
  official repository used for tau2/tau3 style customer-service agent
  evaluation. It includes `airline`, `retail`, `telecom`,
  `banking_knowledge`, and voice data.
- `datasets/bfcl-v4/`: Berkeley Function Calling Leaderboard code and BFCL v4
  data, extracted from the Gorilla repository subdirectory
  `berkeley-function-call-leaderboard`.
- `datasets/appworld/`: AppWorld source snapshot plus official benchmark data
  downloaded into `datasets/appworld/data`.

## Source Snapshots

| Local path | Upstream | Snapshot |
| --- | --- | --- |
| `datasets/tau3-bench` | `https://github.com/sierra-research/tau2-bench` | `1901a301961cbbe3fd11f3e84a2a376530c759e3` |
| `datasets/bfcl-v4` | `https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard` | `6ea57973c7a6097fd7c5915698c54c17c5b1b6c8` |
| `datasets/appworld` | `https://github.com/StonyBrookNLP/appworld` | `a072b7a86e7c1d5b1d7175659d750ebb9b79f10a` |

Downloaded on 2026-07-09.

## Local Notes

- Do not place future benchmark harnesses, result parsers, or run reports in the
  project root. Put them under `eval/`.
- AppWorld GitHub archives contain Git LFS pointer files for some bundles. The
  usable benchmark data was prepared with:

```bash
uvx --from appworld appworld install
uvx --from appworld appworld download data --root eval/datasets/appworld
```

- BFCL v4 data files are under `datasets/bfcl-v4/bfcl_eval/data/`.
- tau3/tau2 task data is under `datasets/tau3-bench/data/tau2/domains/`.

## Smoke Commands

```bash
cd eval/datasets/tau3-bench
uv sync
uv run tau2 run --domain airline --agent-llm <model> --user-llm <model> --num-trials 1 --num-tasks 5
```

```bash
cd eval/datasets/bfcl-v4
pip install -e .
```

```bash
APPWORLD_ROOT=eval/datasets/appworld uvx --from appworld appworld download data --root eval/datasets/appworld
```
