import { useEffect, useRef, useState, type ReactNode } from "react"
import { HttpAgent, type Message } from "@ag-ui/client"
import {
  CopilotKitContext,
  CopilotKitCoreReact,
  EMPTY_SET,
  useCopilotKit,
} from "@copilotkit/react-core/v2/context"
import { useAgent } from "@copilotkit/react-core/v2/headless"
import {
  Blocks,
  MessageSquarePlus,
  Send,
  Sparkles,
  Square,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import type { Conversation } from "@/lib/types"
import type { SuperAgentController } from "@/lib/use-super-agent"
import { cn } from "@/lib/utils"

const COPILOT_AGENT_ID = "super-agent-web"

interface CopilotKitExamplePageProps {
  controller: SuperAgentController
}

export function CopilotKitExamplePage({
  controller,
}: CopilotKitExamplePageProps) {
  const conversation = controller.selectedConversation

  if (!conversation) {
    return (
      <main className="copilotkit-page">
        <CopilotKitHeader conversation={null} />
        <div className="copilotkit-empty">
          <div className="feature-summary-icon">
            <MessageSquarePlus />
          </div>
          <h2>需要一个会话 ID</h2>
          <p>点击后会显式创建会话，再由 CopilotKit 通过 AG-UI 运行。</p>
          <Button
            type="button"
            disabled={controller.busy}
            onClick={() => void controller.createNewConversation()}
          >
            <MessageSquarePlus />
            创建会话
          </Button>
        </div>
      </main>
    )
  }

  return (
    <main className="copilotkit-page">
      <CopilotKitHeader conversation={conversation} />
      <CopilotKitSession
        key={conversation.conversation_id}
        conversation={conversation}
        onRunFinished={() =>
          controller.reloadRuntimeData(conversation.conversation_id)
        }
      />
    </main>
  )
}

function CopilotKitHeader({ conversation }: { conversation: Conversation | null }) {
  return (
    <header className="copilotkit-header">
      <div className="copilotkit-title-row">
        <div className="feature-summary-icon">
          <Blocks />
        </div>
        <div>
          <div className="flex items-center gap-2">
            <h1>CopilotKit 示例</h1>
            <Badge variant="secondary">AG-UI</Badge>
          </div>
          <p>官方 React 客户端直接连接 Super Agent 的标准端点。</p>
        </div>
      </div>
      <div className="copilotkit-connection">
        <code>POST /ag-ui</code>
        {conversation ? (
          <span title={conversation.conversation_id}>{conversation.title}</span>
        ) : null}
      </div>
    </header>
  )
}

function CopilotKitSession({
  conversation,
  onRunFinished,
}: {
  conversation: Conversation
  onRunFinished: () => Promise<void>
}) {
  const [agents] = useState(() => {
    const agent = new HttpAgent({
      agentId: COPILOT_AGENT_ID,
      description: "Super Agent through the AG-UI protocol",
      url: "/ag-ui",
      threadId: conversation.conversation_id,
      initialMessages: conversation.messages.map(toAGUIMessage),
    })
    return { [COPILOT_AGENT_ID]: agent }
  })

  return (
    <section className="copilotkit-chat-frame" aria-label="CopilotKit 对话">
      <HeadlessCopilotKitProvider agents={agents}>
        <HeadlessChat
          agentName={conversation.agent_name}
          labels={{
            empty: "通过 CopilotKit 开始对话",
            placeholder: "向 Super Agent 提问...",
          }}
          onRunFinished={onRunFinished}
        />
      </HeadlessCopilotKitProvider>
    </section>
  )
}

function HeadlessCopilotKitProvider({
  agents,
  children,
}: {
  agents: Record<string, HttpAgent>
  children: ReactNode
}) {
  // Fine-grained public entries keep optional rich-chat renderers out of the bundle.
  const [context] = useState(() => ({
    copilotkit: new CopilotKitCoreReact({ agents__unsafe_dev_only: agents }),
    executingToolCallIds: EMPTY_SET,
  }))
  return (
    <CopilotKitContext.Provider value={context}>
      {children}
    </CopilotKitContext.Provider>
  )
}

interface HeadlessChatProps {
  agentName: string
  labels: { empty: string; placeholder: string }
  onRunFinished: () => Promise<void>
}

function HeadlessChat({ agentName, labels, onRunFinished }: HeadlessChatProps) {
  const { copilotkit } = useCopilotKit()
  const { agent, isReady } = useAgent({ agentId: COPILOT_AGENT_ID })
  const [prompt, setPrompt] = useState("")
  const [runError, setRunError] = useState("")
  const bottomRef = useRef<HTMLDivElement>(null)
  const visibleMessages = agent.messages.flatMap(toVisibleMessage)
  const lastMessage = visibleMessages.at(-1)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
  }, [agent.isRunning, lastMessage?.content])

  async function sendMessage(): Promise<void> {
    const content = prompt.trim()
    if (!content || !isReady || agent.isRunning) {
      return
    }
    setPrompt("")
    setRunError("")
    agent.addMessage({ id: crypto.randomUUID(), role: "user", content })
    try {
      await copilotkit.runAgent({ agent })
    } catch (error) {
      setRunError(readErrorMessage(error))
    } finally {
      try {
        await onRunFinished()
      } catch (error) {
        setRunError(readErrorMessage(error))
      }
    }
  }

  return (
    <>
      <div className="copilotkit-message-scroll">
        <div className="copilotkit-message-column">
          {visibleMessages.length === 0 ? (
            <div className="copilotkit-empty-chat">
              <Sparkles />
              <strong>{labels.empty}</strong>
              <span>消息会由 CopilotKit headless hook 交给 AG-UI。</span>
            </div>
          ) : null}
          {visibleMessages.map((message) => (
            <article
              key={message.id}
              className={cn(
                "chat-message",
                message.role === "user"
                  ? "chat-message-user"
                  : "chat-message-agent"
              )}
            >
              <div className="message-role">
                {message.role === "user" ? "你" : agentName}
              </div>
              <div className="message-content">{message.content}</div>
            </article>
          ))}
          {agent.isRunning && lastMessage?.role !== "assistant" ? (
            <AgentThinking />
          ) : null}
          <div ref={bottomRef} />
        </div>
      </div>
      <footer className="composer-area">
        {runError ? (
          <p className="copilotkit-run-error" role="alert">
            {runError}
          </p>
        ) : null}
        <div className="composer">
          <Textarea
            value={prompt}
            rows={2}
            className="max-h-40 min-h-14 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
            placeholder={labels.placeholder}
            disabled={!isReady || agent.isRunning}
            aria-label="CopilotKit 消息"
            onChange={(event) => setPrompt(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault()
                void sendMessage()
              }
            }}
          />
          {agent.isRunning ? (
            <Button
              type="button"
              variant="outline"
              size="icon"
              onClick={() => copilotkit.stopAgent({ agent })}
              aria-label="停止接收"
            >
              <Square className="fill-current" />
            </Button>
          ) : (
            <Button
              type="button"
              size="icon"
              disabled={!prompt.trim() || !isReady}
              onClick={() => void sendMessage()}
              aria-label="发送消息"
            >
              <Send />
            </Button>
          )}
        </div>
      </footer>
    </>
  )
}

function AgentThinking() {
  return (
    <article className="chat-message chat-message-agent">
      <div className="message-role">Agent</div>
      <div className="message-content">
        <span className="agent-thinking">
          <span />
          <span />
          <span />
        </span>
      </div>
    </article>
  )
}

interface VisibleMessage {
  id: string
  role: "assistant" | "user"
  content: string
}

function toVisibleMessage(message: Message): VisibleMessage[] {
  if (message.role !== "assistant" && message.role !== "user") {
    return []
  }
  const content = readMessageContent(message)
  return content ? [{ id: message.id, role: message.role, content }] : []
}

function readMessageContent(message: Message): string {
  if (!("content" in message)) {
    return ""
  }
  if (typeof message.content === "string") {
    return message.content
  }
  if (!Array.isArray(message.content)) {
    return ""
  }
  return message.content
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("")
}

function readErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "AG-UI 运行失败"
}

function toAGUIMessage(message: Conversation["messages"][number]): Message {
  return {
    id: message.message_id,
    role: message.role,
    content: message.content,
  }
}
