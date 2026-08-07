# General multi-Agent producer-consumer chain

Act as the task producer. Inspect the registered subagents, split only independent work,
and create each unit with an explicit purpose and required features. Dispatch every created
task to a named suitable Agent or let the declared Agent contracts select one.

Each Agent Runtime owns one native task queue and one serial consumer per child Agent.
Different child Agents may work concurrently, while one child processes its own tasks in queue
order. A child Agent with its own children can activate this Skill and repeat the same mechanism
at the next level. Keep task prompts self-contained because a consumer does not inherit the
producer's model context.

After dispatching work, call `wait_for_agent_tasks` instead of repeatedly asking the model
for status. Choose the shortest useful wait and a trigger that matches the dependency:

- `any_task_finished` wakes after any unseen terminal result.
- `any_task_completed` wakes after any unseen success.
- `any_task_failed` wakes after any unseen failure.
- `all_tasks_finished` wakes after all current tasks reach terminal states.
- `selected_tasks_finished` waits for the supplied task IDs.
- `timeout` sleeps until the bounded timeout.

The configured wait limit caps each requested wait. Inspect failures explicitly, cancel only
work that has not started, and finish only after every created task is completed, failed, or
cancelled. Synthesize the final answer from actual child results; never invent missing work.

## Budgeted decision groups

Use a group only when independent evidence can change a consequential or uncertain decision.
Routine work should stay a single task. For a group, create one shared task packet and give each
member a different role such as proposal, counterexample, or verification. Runtime sends the
shared packet once through central progressive disclosure; member prompts contain only the packet
reference, role difference, and output contract.

Wait with `wait_for_agent_group`. A member must return the declared JSON decision protocol. Two
support votes satisfy the default three-member quorum; two independent reject votes are required
to reject. One member failure does not reject the proposal. Split votes, malformed output, missing
measurements, and implementation failures remain `inconclusive`.

The Skill checks model diversity and estimated cost before creating member tasks. A budget failure
returns `budget_exceeded` without dispatch. A smaller group is allowed only when the Skill setting
explicitly enables it, and the result then records `reduced = true`.
