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
import { Empty, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
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
          <h2>长期记忆</h2>
          <p>{props.memory.length} 条可在后续会话中回忆的内容</p>
        </div>
        <HelpTooltip label="当前会话消息就是短期记忆，不写入记忆存储。这里只保存抽象、关键、稳定或习惯性的长期信息。模型可在回忆时显式整理或遗忘它们。" />
      </div>
      {props.memory.length ? (
        <div className="memory-list">
          {props.memory.map((item) => (
            <article key={item.memory_id} className="memory-row">
              <div className="memory-icon">
                <Brain />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm leading-6">{item.text}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant="outline">长期</Badge>
                  {item.labels.map((label) => (
                    <Badge key={label} variant="outline">
                      {label}
                    </Badge>
                  ))}
                  <span className="inline-flex items-center gap-1">
                    <Clock3 className="size-3" />
                    {formatDate(item.created_at)}
                  </span>
                  {item.source ? (
                    <span>{item.source}</span>
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
            <EmptyTitle>暂无长期记忆</EmptyTitle>
          </EmptyHeader>
        </Empty>
      )}
    </section>
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
            onClick={() => props.onForget(props.item.memory_id)}
          >
            遗忘
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateFormatter.format(date)
}
