import { useEffect, useRef, useState } from "react"
import { Eraser, Send, Square, WandSparkles } from "lucide-react"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { Conversation } from "@/lib/types"
import { cn } from "@/lib/utils"

interface ChatWorkspaceProps {
  conversation: Conversation | null
  pendingPrompt: string
  streamingResponse: string
  runActivity: string
  running: boolean
  busy: boolean
  onSend: (prompt: string) => void
  onStop: () => void
  onClear: (conversationId: string) => void
  onCreate: () => void
}

export function ChatWorkspace(props: ChatWorkspaceProps) {
  const [prompt, setPrompt] = useState("")
  const bottomRef = useRef<HTMLDivElement>(null)
  const messages = props.conversation?.messages || []

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
  }, [messages.length, props.pendingPrompt, props.streamingResponse])

  function submit(): void {
    if (!prompt.trim() || props.running) {
      return
    }
    props.onSend(prompt)
    setPrompt("")
  }

  return (
    <section className="chat-workspace">
      <header className="chat-header">
        <div className="min-w-0">
          <p className="section-eyebrow">
            {props.conversation?.agent_name || "Agent"}
          </p>
          <h1 className="truncate text-base font-semibold">
            {props.conversation?.title || "新对话"}
          </h1>
        </div>
        {props.conversation ? (
          <AlertDialog>
            <Tooltip>
              <TooltipTrigger asChild>
                <AlertDialogTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    disabled={props.busy || props.running}
                    aria-label="清空当前对话"
                  >
                    <Eraser />
                  </Button>
                </AlertDialogTrigger>
              </TooltipTrigger>
              <TooltipContent side="bottom">清空对话</TooltipContent>
            </Tooltip>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>清空当前对话？</AlertDialogTitle>
                <AlertDialogDescription>
                  会话会保留，但当前消息将从活动视图中清除。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction
                  variant="destructive"
                  onClick={() =>
                    props.onClear(props.conversation!.conversation_id)
                  }
                >
                  清空
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        ) : null}
      </header>
      <ScrollArea className="min-h-0 flex-1">
        <div className="chat-message-column">
          {messages.length === 0 && !props.pendingPrompt ? (
            <Empty className="min-h-80 border-0">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <WandSparkles />
                </EmptyMedia>
                <EmptyTitle>开始一段新对话</EmptyTitle>
                <EmptyDescription>Skill is all you need.</EmptyDescription>
              </EmptyHeader>
              {!props.conversation ? (
                <Button type="button" onClick={props.onCreate}>
                  新建会话
                </Button>
              ) : null}
            </Empty>
          ) : null}
          {messages.map((message) => (
            <article
              key={message.message_id}
              className={cn(
                "chat-message",
                message.role === "user"
                  ? "chat-message-user"
                  : "chat-message-agent"
              )}
            >
              <div className="message-role">
                {message.role === "user"
                  ? "你"
                  : props.conversation?.agent_name}
              </div>
              <div className="message-content">{message.content}</div>
            </article>
          ))}
          {props.pendingPrompt ? (
            <article className="chat-message chat-message-user">
              <div className="message-role">你</div>
              <div className="message-content">{props.pendingPrompt}</div>
            </article>
          ) : null}
          {props.running ? (
            <article className="chat-message chat-message-agent">
              <div className="message-role">Agent</div>
              <div className="message-content">
                {props.streamingResponse || (
                  <span className="agent-thinking">
                    <span />
                    <span />
                    <span />
                  </span>
                )}
              </div>
            </article>
          ) : null}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>
      <footer className="composer-area">
        {props.runActivity ? (
          <div className="run-activity">
            <span className="run-activity-pulse" />
            {props.runActivity}
          </div>
        ) : null}
        <div className="composer">
          <Textarea
            value={prompt}
            rows={2}
            className="max-h-40 min-h-14 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
            placeholder="输入消息"
            disabled={props.running}
            aria-label="消息"
            onChange={(event) => setPrompt(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault()
                submit()
              }
            }}
          />
          {props.running ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={props.onStop}
                  aria-label="停止接收"
                >
                  <Square className="fill-current" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">停止接收</TooltipContent>
            </Tooltip>
          ) : (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  size="icon"
                  disabled={!prompt.trim()}
                  onClick={submit}
                  aria-label="发送消息"
                >
                  <Send />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">发送消息</TooltipContent>
            </Tooltip>
          )}
        </div>
      </footer>
    </section>
  )
}
