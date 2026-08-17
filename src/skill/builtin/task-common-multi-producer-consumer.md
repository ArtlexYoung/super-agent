+++
name = "common-multi-producer-consumer"
type = "task"
description = "Coordinate child Agents through owned queues and event-driven completion"
version = "0.2.1"
created_by = "builtin"
agent_can_update = false
categories = ["general/multi-agent", "coordination/producer-consumer"]
requires = ["list_agent_tree", "create_agent_task", "dispatch_agent_task", "read_agent_tasks", "wait_for_agent_tasks", "cancel_agent_task", "post_shared_note", "read_shared_notes", "wait_for_shared_notes", "create_agent_decision", "wait_for_agent_decision", "read_agent_decisions"]
+++
# Multi-Agent producer-consumer method

Act as task producer. Inspect the Agent tree, split only independent work, and create self-contained tasks with a purpose and required features. Target an Agent group when work belongs to one department; otherwise let the current group select a suitable child. Each Agent consumes its own queue serially while different Agents may run concurrently. A child with its own subtree can activate this same Skill at the next level.

After dispatch, call `wait_for_agent_tasks` instead of polling with model calls. Choose a bounded timeout and one trigger: any finish, any success, any failure, all finish, selected tasks finish, or timeout. Pass the returned version to later waits so an old completion does not wake the same plan repeatedly. Inspect failures explicitly and cancel only work that has not started.

Sibling groups exchange only explicit notes through their parent shared board. Post bounded findings with a stable disclosure reference, wait for new notes instead of polling, and supersede stale notes explicitly.

Use a staged Agent decision only when independent evidence can change a consequential or uncertain decision. The first member scouts, a second independently validates, and a third runs only when votes conflict, fail, or remain inconclusive. Two support votes promote and two reject votes reject by default. Malformed output, split evidence, or member failure is inconclusive. Budget and model-diversity failures are explicit; never silently reduce a decision.

Finish only after every created task is completed, failed, or cancelled. Synthesize from actual child results without inventing missing work.
