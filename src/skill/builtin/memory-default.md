+++
name = "default"
type = "memory"
description = "Temporary working context and revisable long-term experience"
version = "0.2.0"
created_by = "builtin"
agent_can_update = false
categories = ["memory/general"]
requires = ["remember_temporary", "remember_long_term", "recall_memory", "promote_temporary_memory", "organize_long_term_memory"]
+++
# Memory method

Use temporary memory only for working context in the current conversation. It must never appear in another conversation.

Use long-term memory only for abstract, important, stable, or habitual experience. A memory is revisable evidence, not guaranteed truth. Preserve its source and context. During recall, compare relevant long-term experience with current temporary context. Explicitly promote a temporary item only after abstracting away incidental conversation details.

When evidence changes, explicitly revise, relate, mark a contradiction, merge, split, forget, or restore long-term items. Recall itself is a read and must not mutate memory. Make no change when existing experience remains useful and consistent.
