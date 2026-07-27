import type {
  AGUIEvent,
  AgentConfiguration,
  BootstrapData,
  Conversation,
  ModelSkillInput,
  RunInsight,
} from "@/lib/types"

async function requestJSON<Result>(
  path: string,
  init?: RequestInit
): Promise<Result> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body
      ? { "Content-Type": "application/json", ...init.headers }
      : init?.headers,
  })
  const value: unknown = await response.json()
  if (!response.ok) {
    const message = readErrorMessage(value)
    throw new Error(message || `请求失败（${response.status}）`)
  }
  return value as Result
}

export function loadBootstrap(): Promise<BootstrapData> {
  return requestJSON("/api/bootstrap")
}

export function createConversation(title = ""): Promise<Conversation> {
  return requestJSON("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title }),
  })
}

export function renameConversation(
  conversationId: string,
  title: string
): Promise<Conversation> {
  return requestJSON(
    `/api/conversations/${encodeURIComponent(conversationId)}`,
    {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }
  )
}

export function clearConversation(
  conversationId: string
): Promise<Conversation> {
  return requestJSON(
    `/api/conversations/${encodeURIComponent(conversationId)}/clear`,
    { method: "POST" }
  )
}

export function deleteConversation(conversationId: string): Promise<void> {
  return requestJSON(
    `/api/conversations/${encodeURIComponent(conversationId)}`,
    {
      method: "DELETE",
    }
  )
}

export function saveAgentConfiguration(
  configuration: AgentConfiguration
): Promise<BootstrapData> {
  return requestJSON("/api/config", {
    method: "PUT",
    body: JSON.stringify(configuration),
  })
}

export function saveModelSkill(model: ModelSkillInput): Promise<BootstrapData> {
  return requestJSON("/api/models", {
    method: "POST",
    body: JSON.stringify(model),
  })
}

export function deleteModelSkill(name: string): Promise<BootstrapData> {
  return requestJSON(`/api/models/${encodeURIComponent(name)}`, {
    method: "DELETE",
  })
}

export function forgetMemory(itemId: string): Promise<BootstrapData> {
  return requestJSON(`/api/memory/${encodeURIComponent(itemId)}`, {
    method: "DELETE",
  })
}

export function loadRunInsight(runId: string): Promise<RunInsight> {
  return requestJSON(`/api/runs/${encodeURIComponent(runId)}`)
}

export async function streamAgentRun(
  conversationId: string,
  prompt: string,
  onEvent: (event: AGUIEvent) => void,
  signal: AbortSignal
): Promise<void> {
  const response = await fetch("/ag-ui", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      threadId: conversationId,
      runId: crypto.randomUUID(),
      state: {},
      messages: [
        {
          id: crypto.randomUUID(),
          role: "user",
          content: prompt,
        },
      ],
      tools: [],
      context: [],
      forwardedProps: {},
    }),
    signal,
  })
  if (!response.ok || !response.body) {
    const body: unknown = await response.json().catch(() => ({}))
    throw new Error(
      readErrorMessage(body) || `运行请求失败（${response.status}）`
    )
  }
  await readSSEStream(response.body, onEvent, signal)
}

async function readSSEStream(
  stream: ReadableStream<Uint8Array>,
  onEvent: (event: AGUIEvent) => void,
  signal: AbortSignal
): Promise<void> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  while (!signal.aborted) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const blocks = buffer.split(/\r?\n\r?\n/)
    buffer = blocks.pop() || ""
    for (const block of blocks) {
      emitSSEBlock(block, onEvent)
    }
    if (done) {
      break
    }
  }
  if (buffer.trim()) {
    emitSSEBlock(buffer, onEvent)
  }
}

function emitSSEBlock(
  block: string,
  onEvent: (event: AGUIEvent) => void
): void {
  const data = block
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n")
  if (data) {
    onEvent(JSON.parse(data) as AGUIEvent)
  }
}

function readErrorMessage(value: unknown): string {
  if (
    typeof value === "object" &&
    value !== null &&
    "error" in value &&
    typeof value.error === "string"
  ) {
    return value.error
  }
  return ""
}
