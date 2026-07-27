Decompose the task into the fewest independently executable steps.

Return only one JSON object with a `steps` array. Every step must contain exactly
`instruction`, `purpose`, `required_features`, and `subagent`. Use a short model
purpose that describes the work. Include `text` in required features and add
`tools` only when runtime tools are needed. Set `subagent` to one available name
or null. The final step must synthesize the completed work into the user-facing
answer.
