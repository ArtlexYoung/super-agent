---
name: "common-multi-review"
description: "Coordinate several independent Agents to find defects, risks, questions, and improvements, then cross-check the findings"
metadata:
  super-agent-agent-can-update: "false"
  super-agent-categories: "[\"general/review\",\"review/multi-agent\"]"
  super-agent-created-by: "builtin"
  super-agent-includes: "[\"task:common-multi-producer-consumer\"]"
  super-agent-requires: "[\"dispatch_agent_tasks\"]"
  super-agent-type: "task"
  super-agent-version: "0.2.1"
---
# Multi-Agent review method

Use this method to review an existing result, plan, decision, Skill, workflow, prompt, document, or code change with several independent Agents. This is not executor self-check and not a simple vote. The goal is to discover concrete defects, risks, unanswered questions, and worthwhile improvements.

## Prepare one neutral packet

Create a bounded review packet containing the original request, expected outcome, constraints, acceptance evidence, artifact or disclosure references, executed checks, and known limitations. Separate observed facts from interpretations. Do not include hidden reasoning or the executor's claim that its own result is correct. Large content stays behind stable disclosure references instead of being copied into every task.

Review is read-only unless the user separately authorizes changes. A review task may reproduce checks through already registered tools, but it must not edit the reviewed artifact.

## Run independent first reviews

Inspect the Agent tree and derive complementary review scopes from the actual target and its risks. Do not use a fixed trigger-word table or a permanent role list. Create at least two tasks with the same target group, purpose, and required features, then call `dispatch_agent_tasks` once so different Agents receive them in parallel. Request different models when model diversity is material and configured.

If fewer than two compatible Agents are available, report that multi-Agent review cannot run. Do not silently replace it with self-review or one-Agent review. If different-model dispatch is unavailable, make the reduced diversity explicit before deciding whether a distinct-Agent review is still useful.

Each first reviewer must inspect independently before reading another review. It returns one JSON object with this contract:

```json
{
  "summary": "bounded independent assessment",
  "findings": [
    {
      "local_id": "finding-1",
      "kind": "defect | risk | improvement | question",
      "severity": "critical | high | medium | low",
      "location": "artifact path, section, symbol, or stable reference",
      "claim": "one precise issue or opportunity",
      "impact": "why it matters",
      "evidence": "observed or reproducible evidence",
      "suggestion": "smallest useful response",
      "expected_benefit": "benefit and relevant tradeoff",
      "verification": "how to confirm the response",
      "confidence": 0.0
    }
  ],
  "coverage": ["areas actually inspected"],
  "limitations": ["missing evidence or inaccessible areas"]
}
```

An empty `findings` array is valid only when the reviewer states meaningful coverage. Speculation without a location, evidence path, or testable claim is a question or limitation, not a confirmed defect.

## Collect before synthesis

Wait for the selected first-review tasks instead of polling with model calls. Do not synthesize until every selected task is terminal. A failed, timed-out, or malformed review is missing evidence, never approval. Preserve its failure in the final limitations.

Assign stable global finding IDs and make a bounded findings packet. Merge only findings that share the same claim and evidence; similar wording alone is not a duplicate. Remove reviewer identity from the packet so status and model reputation do not anchor the next pass.

## Cross-check the findings

Create a second batch for independent cross-review. Give each cross-reviewer findings it did not author when that can be established, and prefer a different Agent or model from the original reviewer. Cross-reviewers should reproduce evidence, search for counterexamples, identify duplicates, correct severity, and add missed optimization costs or regressions.

Each cross-review response maps stable finding IDs to `confirmed`, `refuted`, `needs_evidence`, or `duplicate`, followed by concise evidence, any corrected wording or severity, and confidence. It may add new findings using the first-review contract. Do not discard a minority finding merely because other reviewers missed it.

## Resolve consequential disputes

Evidence outranks vote count. Keep well-supported minority findings. Use `create_agent_decision` only for a consequential finding whose evidence remains disputed after cross-review. Select roles that fit that specific dispute and ask members to reproduce or falsify the claim. The staged decision may stop at quorum; it is a focused adjudication step, not the discovery mechanism.

## Report and follow up

Return findings first, ordered by impact and evidence strength. For every retained item, report kind, severity, location, impact, evidence, suggested response, expected benefit or tradeoff, verification, confidence, and cross-review status. Then report unresolved disagreements, coverage, failed reviewers, and remaining limitations. Distinguish correctness defects from optional improvements so optimization ideas do not hide blockers.

Use one final status: `changes_required`, `acceptable_with_improvements`, `no_material_findings`, or `inconclusive`. Do not modify the target as a side effect of review. If changes are requested, create separate implementation tasks and review the changed evidence again with an Agent or model that did not own the modification whenever possible.
