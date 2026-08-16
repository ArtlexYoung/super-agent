+++
name = "common-multi-producer-consumer"
type = "task"
description = "Coordinate child Agents through owned queues and event-driven completion"
version = "0.2.0"
created_by = "builtin"
agent_can_update = false
categories = ["general/multi-agent", "coordination/producer-consumer"]
requires = ["list_subagents", "create_agent_task", "dispatch_agent_task", "read_agent_tasks", "wait_for_agent_tasks", "cancel_agent_task", "create_agent_group", "wait_for_agent_group", "read_agent_groups"]
+++
# Multi-Agent producer-consumer method

Act as task producer. Inspect configured child Agents, split only independent work, and create self-contained tasks with a purpose and required features. Dispatch each created task to a suitable Agent. Each child consumes its own queue serially while different children may run concurrently. A child with children can activate this same Skill at the next level.

After dispatch, call `wait_for_agent_tasks` instead of polling with model calls. Choose a bounded timeout and one trigger: any finish, any success, any failure, all finish, selected tasks finish, or timeout. Pass the returned version to later waits so an old completion does not wake the same plan repeatedly. Inspect failures explicitly and cancel only work that has not started.

Use a staged decision group only when independent evidence can change a consequential or uncertain decision. The first member scouts, a second independently validates, and a third runs only when votes conflict, fail, or remain inconclusive. Two support votes promote and two reject votes reject by default. Malformed output, split evidence, or member failure is inconclusive. Budget and model-diversity failures are explicit; never silently reduce a group.

Finish only after every created task is completed, failed, or cancelled. Synthesize from actual child results without inventing missing work.
