import { Brain, Clock3, Trash2 } from "lucide-react"

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
import {
  Empty,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import type { MemoryItem } from "@/lib/types"

interface MemoryConfigurationProps {
  memory: MemoryItem[]
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
  return (
    <section className="configuration-section">
      <div className="configuration-section-header">
        <div>
          <h2>活动记忆</h2>
          <p>{props.memory.length} 条当前可回忆内容</p>
        </div>
        <HelpTooltip label="回忆时 Runtime 会整理重复、过时和低价值内容；遗忘只移出活动视图，历史事件仍可审计。" />
      </div>
      {props.memory.length ? (
        <div className="memory-list">
          {props.memory.map((item) => (
            <article key={item.item_id} className="memory-row">
              <div className="memory-icon">
                <Brain />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm leading-6">{item.text}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant="outline">{scopeLabel(item.scope)}</Badge>
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
                    <Trash2 />
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
                      onClick={() => props.onForget(item.item_id)}
                    >
                      遗忘
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </article>
          ))}
        </div>
      ) : (
        <Empty className="min-h-64 border-0">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Brain />
            </EmptyMedia>
            <EmptyTitle>暂无活动记忆</EmptyTitle>
          </EmptyHeader>
        </Empty>
      )}
    </section>
  )
}

function scopeLabel(scope: string): string {
  const labels: Record<string, string> = {
    agent: "Agent",
    user: "用户",
    project: "项目",
    conversation: "会话",
  }
  return labels[scope] || scope
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateFormatter.format(date)
}

function shortId(value: string): string {
  return value.length > 12 ? value.slice(0, 12) : value
}
