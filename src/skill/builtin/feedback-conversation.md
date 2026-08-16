+++
name = "conversation"
type = "feedback"
description = "Judge whether a follow-up evaluates or corrects a previous response"
version = "0.2.0"
created_by = "builtin"
agent_can_update = false
categories = ["evaluation/conversation"]
+++
Judge from the complete conversation whether the follow-up evaluates or corrects the previous response. Do not infer feedback from fixed trigger words. Return only the response contract requested by the caller, with a score and concise reason grounded in conversation evidence.
