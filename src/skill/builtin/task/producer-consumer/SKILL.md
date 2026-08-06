# Producer-consumer task chain

Act as the task producer. Inspect the registered subagents, split only independent work,
and create each unit with an explicit purpose and required features. Dispatch every created
task to a named suitable Agent or let the declared Agent contracts select one.

Each Agent owns one serial task consumer. Different Agents may work concurrently, while one
Agent processes its own tasks in queue order. Keep task prompts self-contained because a
consumer does not inherit the producer's model context.

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
