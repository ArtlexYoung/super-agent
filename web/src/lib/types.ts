export type AppPage = "conversations" | "copilotkit" | "configuration"

export interface AgentConfiguration {
  name: string
  system: string
  skills: string[]
  max_agent_chain_depth: number | null
  disabled_skills: string[]
}

export interface ConversationMessage {
  message_id: string
  role: "user" | "assistant"
  content: string
  created_at: string
  run_id: string
  run_result: Record<string, unknown> | null
}

export interface Conversation {
  conversation_id: string
  user_id: string
  agent_name: string
  title: string
  created_at: string
  updated_at: string
  messages: ConversationMessage[]
}

export interface RunSnapshot {
  run_id: string
  user_id: string
  conversation_id: string | null
  agent_name: string
  parent_run_id: string | null
  status: string
  prompt: string
  started_at: string
  finished_at: string | null
  event_count: number
  last_event_type: string
  runtime_lock_sha256: string | null
  workflow: string | null
  used_skills: string[]
  stop_reason: string | null
  error: Record<string, string> | null
}

export interface SkillState {
  key: string
  name: string
  type: string
  description: string
  version: string
  triggers: string[]
  provides: string[]
  requires: string[]
  content_sha256: string
  agent_created: boolean
  agent_can_update: boolean
  freshness: number
  function_group: string
  freshness_updated_at: string
  call_count: number
  success_count: number
  same_function_successful_followups: number
  default: boolean
  enabled: boolean
  selected: boolean
}

export interface ModelProfile {
  key: string
  name: string
  description: string
  version: string
  provider: string
  model: string
  base_url: string | null
  api_key_env: string | null
  supports: string[]
  purposes: string[]
  strengths: string[]
  triggers: string[]
  default: boolean
  quality_score: number
  expected_latency_ms: number
  input_cost_per_million: number
  output_cost_per_million: number
  source: string
  skill_key: string | null
  content_sha256: string | null
  agent_created: boolean
  agent_can_update: boolean
  agent_can_update_connection: boolean
  ready: boolean
}

export interface ModelSkillInput {
  name: string
  description: string
  provider: string
  model: string
  base_url: string
  api_key_env: string
  supports: string[]
  purposes: string[]
  strengths: string[]
  triggers: string[]
  default: boolean
  agent_can_update: boolean
  agent_can_update_connection: boolean
  quality_score: number | null
  expected_latency_ms: number | null
  input_cost_per_million: number | null
  output_cost_per_million: number | null
  previous_name: string
}

export interface MemoryItem {
  item_id: string
  text: string
  scope: string
  source_run_id: string
  created_at: string
  memory_type: "temporary" | "long_term"
  conversation_id: string | null
}

export interface SubagentNode {
  name: string
  description: string
  agent_name: string
  created_by_agent: boolean
  path: string[]
  runs: RunSnapshot[]
  children: SubagentNode[]
}

export interface BootstrapData {
  schema_version: number
  agent: AgentConfiguration
  storage: { backend: string; path: string }
  configuration_path: string
  skills: SkillState[]
  models: ModelProfile[]
  conversations: Conversation[]
  runs: RunSnapshot[]
  memory: MemoryItem[]
  subagents: SubagentNode[]
}

export interface RunInsight {
  schema_version: number
  snapshot: RunSnapshot
  plan: Record<string, unknown>
  task_plan: Record<string, unknown>
  steps: Array<Record<string, unknown>>
  model_calls: Array<Record<string, unknown>>
  routing_evidence: Array<Record<string, unknown>>
  skill_freshness: Array<Record<string, unknown>>
  evolution: Array<Record<string, unknown>>
  events: Array<Record<string, unknown>>
}

export interface AGUIEvent {
  type: string
  delta?: string
  message?: string
  name?: string
  value?: Record<string, unknown>
}
