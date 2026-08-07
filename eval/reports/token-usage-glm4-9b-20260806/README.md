# Token Usage Benchmark

Model: `THUDM/GLM-4-9B-0414`. Token counts are the model provider's returned usage, captured by the local loopback adapter.

## Tested Versions

| Agent | Version | HumanEval+ run | LiveCodeBench Codegen run |
| --- | --- | --- | --- |
| Codex | `codex-cli 0.146.0` | `token-usage-he-codex-20260806` | `token-usage-lcb-codex-20260806` |
| Claude Code | `2.1.221 (Claude Code)` | `token-usage-he-claude-20260806` | `token-usage-lcb-claude-20260806` |
| Super Agent | `0.1.0 (ccac936326ada84b2a3a6ebdb52b3d432130bacc)` | `humaneval-plus-glm4-9b-20260805` | `livecodebench-glm4-9b-20260806` |
| Raw Model | `direct OpenAI Chat Completions (temperature=0, max_tokens=8192)` | `raw-model-he-glm4-9b-20260807` | `raw-model-lcb-glm4-9b-20260807` |

## Aggregate Usage

| Dataset | Agent | Tasks | Calls | Input | Output | Total | Tokens/task | P50 | P95 | Pass rate | Tokens/pass |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| humaneval_plus | Codex | 164 | 164 | 1,215,844 | 9,707 | 1,225,551 | 7,472.87 | 7,454 | 7,642 | 68.29% | 10,942.42 |
| humaneval_plus | Claude Code | 164 | 379 | 390,284 | 15,806 | 406,090 | 2,476.16 | 2,106 | 4,395 | 63.41% | 3,904.71 |
| humaneval_plus | Super Agent | 164 | 164 | 159,009 | 9,031 | 168,040 | 1,024.63 | 1,016 | 1,151 | 62.80% | 1,631.46 |
| humaneval_plus | Raw Model | 164 | 164 | 28,834 | 9,251 | 38,085 | 232.23 | 210 | 389 | 65.24% | 355.93 |
| livecodebench_codegen | Codex | 612 | 612 | 4,794,651 | 92,614 | 4,887,265 | 7,985.73 | 7,915 | 8,553 | 27.94% | 28,580.50 |
| livecodebench_codegen | Claude Code | 612 | 1,268 | 1,826,964 | 133,706 | 1,960,670 | 3,203.71 | 3,017 | 4,939 | 24.84% | 12,899.14 |
| livecodebench_codegen | Super Agent | 612 | 612 | 794,459 | 108,532 | 902,991 | 1,475.48 | 1,440 | 1,902 | 25.49% | 5,788.40 |
| livecodebench_codegen | Raw Model | 612 | 612 | 374,968 | 103,688 | 478,656 | 782.12 | 717 | 1,413 | 33.01% | 2,369.58 |

## Both Datasets

| Agent | Tasks | Calls | Input | Output | Total | Tokens/task | Tokens/call | Pass rate | Tokens/pass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Codex | 776 | 776 | 6,010,495 | 102,321 | 6,112,816 | 7,877.34 | 7,877.34 | 36.47% | 21,600.06 |
| Claude Code | 776 | 1,647 | 2,217,248 | 149,512 | 2,366,760 | 3,049.95 | 1,437.01 | 32.99% | 9,245.16 |
| Super Agent | 776 | 776 | 953,468 | 117,563 | 1,071,031 | 1,380.19 | 1,380.19 | 33.38% | 4,135.25 |
| Raw Model | 776 | 776 | 403,802 | 112,939 | 516,741 | 665.90 | 665.90 | 39.82% | 1,672.30 |

## Per-Task Tables

- [HumanEval+](humaneval_plus.md)
- [LiveCodeBench Codegen](livecodebench_codegen.md)
- [Machine-readable CSV](task-token-usage.csv)
- [Structured summary](summary.json)

## Method

Each isolated task receives a signed local proxy token that identifies only the run, dataset, agent, and task key. The adapter verifies that token and records the upstream provider's `input_tokens`, `output_tokens`, and `total_tokens` for every successful model response. Prompts, model outputs, and credentials are not included in this report.

Codex and Claude Code were rerun for this report. Super Agent values come from the retained historical runs listed above; its original runner did not persist a version field, so `0.1.0 (ccac936)` is derived from the source commit checked out immediately before those run timestamps. Raw Model is a direct Chat Completions baseline with no agent runtime; the official test harness is used only after generation to calculate benchmark scores. Treat cross-run conclusions accordingly.
