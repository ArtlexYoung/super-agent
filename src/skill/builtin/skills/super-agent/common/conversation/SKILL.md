---
name: "conversation"
description: "Judge whether a follow-up evaluates or corrects a previous response"
metadata:
  super-agent-categories: "[\"evaluation/conversation\"]"
  super-agent-type: "feedback"
  super-agent-version: "0.2.22"
---
Judge from the complete conversation whether the follow-up evaluates or corrects the previous response. Do not infer feedback from fixed trigger words. Return only the response contract requested by the caller, with a score and concise reason grounded in conversation evidence.
