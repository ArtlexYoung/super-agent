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
  prompt: unknown
  started_at: string
  finished_at: string | null
  event_count: number
  last_event_type: string
  workflow: string | null
  used_skills: string[]
  stop_reason: string | null
  error: Record<string, unknown> | null
}

export interface SkillState {
  key: string
  name: string
  type: string
  description: string
  version: string
  categories: string[]
  requires: string[]
  optional_tools: string[]
  content_sha256: string
  agent_created: boolean
  agent_can_update: boolean
  freshness: number
  call_count: number
  success_count: number
}

export interface ModelProfile {
  key: string
  name: string
  description: string
  provider: string
  model: string
  base_url: string | null
  api_key_env: string | null
  supports: string[]
  purposes: string[]
  weight: number
  default: boolean
  input_cost_per_million: number
  output_cost_per_million: number
  cache_creation_cost_per_million: number
  cache_read_cost_per_million: number
  ready: boolean
}

export interface ModelInput {
  name: string
  description: string
  provider: string
  model: string
  base_url: string
  api_key_env: string
  supports: string[]
  purposes: string[]
  weight: number
  default: boolean
  input_cost_per_million: number | null
  output_cost_per_million: number | null
  cache_creation_cost_per_million: number | null
  cache_read_cost_per_million: number | null
  previous_name: string
}

export interface MemoryItem {
  memory_id: string
  text: string
  lifetime: "temporary" | "long_term"
  labels: string[]
  source: string
  context: string
  conversation_id: string | null
  source_ids: string[]
  status: string
  created_at: string
  updated_at: string
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
  model_calls: Array<Record<string, unknown>>
  model_usage: Array<Record<string, unknown>>
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
