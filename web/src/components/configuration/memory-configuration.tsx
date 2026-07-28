import { Brain, Clock3, MessagesSquare, Trash2 } from "lucide-react"

import { HelpTooltip } from "@/components/shared/help-tooltip"
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Empty, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import type { Conversation, MemoryItem } from "@/lib/types"

interface MemoryConfigurationProps {
  memory: MemoryItem[]
  conversations: Conversation[]
  busy: boolean
  onForget: (itemId: string) => void
}

interface MemoryGroupProps {
  memoryType: MemoryItem["memory_type"]
  title: string
  description: string
  help: string
  items: MemoryItem[]
  conversations: Map<string, string>
  busy: boolean
  onForget: (itemId: string) => void
}

const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
})

export function MemoryConfiguration(props: MemoryConfigurationProps) {
  const temporary = props.memory.filter(
    (item) => item.memory_type === "temporary",
  )
  const longTerm = props.memory.filter(
    (item) => item.memory_type === "long_term",
  )
  const conversations = new Map(
    props.conversations.map((item) => [
      item.conversation_id,
      item.title || "未命名会话",
    ]),
  )

  return (
    <section className="configuration-section">
      <div className="configuration-section-header">
        <div>
          <h2>活动记忆</h2>
          <p>{props.memory.length} 条当前可管理内容</p>
        </div>
        <HelpTooltip label="临时记忆与会话严格绑定，长期记忆跨会话使用；回忆整理不会跨越这两类边界。" />
      </div>
      <div className="flex flex-col gap-10">
        <MemoryGroup
          memoryType="temporary"
          title="临时记忆"
          description={`${temporary.length} 条，仅用于各自所属的当前会话`}
          help="用于任务细节和当前上下文。其他会话无法读取、整理或遗忘这些内容，因此不会污染长期记忆。"
          items={temporary}
          conversations={conversations}
          busy={props.busy}
          onForget={props.onForget}
        />
        <MemoryGroup
          memoryType="long_term"
          title="长期记忆"
          description={`${longTerm.length} 条，可在后续会话中回忆`}
          help="只应保存抽象、关键、重要、稳定或习惯性的内容。回忆时可显式合并、替换、归档或遗忘。"
          items={longTerm}
          conversations={conversations}
          busy={props.busy}
          onForget={props.onForget}
        />
      </div>
    </section>
  )
}

function MemoryGroup(props: MemoryGroupProps) {
  const temporary = props.memoryType === "temporary"
  const Icon = temporary ? MessagesSquare : Brain

  return (
    <div>
      <div className="mb-3 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold">{props.title}</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {props.description}
          </p>
        </div>
        <HelpTooltip label={props.help} />
      </div>
      {props.items.length ? (
        <div className="memory-list">
          {props.items.map((item) => (
            <article key={item.item_id} className="memory-row">
              <div className="memory-icon">
                <Icon />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm leading-6">{item.text}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant="outline">
                    {item.memory_type === "temporary" ? "临时" : "长期"}
                  </Badge>
                  <Badge variant="outline">{scopeLabel(item.scope)}</Badge>
                  {item.conversation_id ? (
                    <span>
                      会话：
                      {conversationLabel(
                        item.conversation_id,
                        props.conversations,
                      )}
                    </span>
                  ) : null}
                  <span className="inline-flex items-center gap-1">
                    <Clock3 className="size-3" />
                    {formatDate(item.created_at)}
                  </span>
                  {item.source_run_id ? (
                    <span className="font-mono">
                      {shortId(item.source_run_id)}
                    </span>
                  ) : null}
                </div>
              </div>
              <ForgetMemoryButton
                item={item}
                busy={props.busy}
                onForget={props.onForget}
              />
            </article>
          ))}
        </div>
      ) : (
        <Empty className="min-h-28 border border-dashed">
          <EmptyHeader>
            <EmptyTitle>暂无{props.title}</EmptyTitle>
          </EmptyHeader>
        </Empty>
      )}
    </div>
  )
}

function ForgetMemoryButton(props: {
  item: MemoryItem
  busy: boolean
  onForget: (itemId: string) => void
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          disabled={props.busy}
          className="text-muted-foreground hover:text-destructive"
          aria-label="遗忘此记忆"
        >
          <Trash2 data-icon="inline-start" />
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>遗忘此条记忆？</AlertDialogTitle>
          <AlertDialogDescription>
            它将不再参与回忆，Runtime 会保留不可变的操作历史。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={() => props.onForget(props.item.item_id)}
          >
            遗忘
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function scopeLabel(scope: string): string {
  const labels: Record<string, string> = {
    agent: "Agent",
    user: "用户",
    project: "项目",
  }
  return labels[scope] || scope
}

function conversationLabel(
  conversationId: string,
  conversations: Map<string, string>,
): string {
  const title = conversations.get(conversationId)
  return title
    ? `${title} · ${shortId(conversationId)}`
    : shortId(conversationId)
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateFormatter.format(date)
}

function shortId(value: string): string {
  return value.length > 12 ? value.slice(0, 12) : value
}
