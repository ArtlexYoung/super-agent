Create the fewest independently executable and verifiable steps needed to complete the task.

Return only one JSON object with a `steps` array. Every step must contain exactly `instruction`, `purpose`, `required_features`, and `subagent`. Use a short lowercase purpose. Include `text` in `required_features` and include `tools` only when the step needs Runtime tools. Set `subagent` to one available name or null. Order dependencies before dependent work. The final step must verify and produce the user-facing result.
