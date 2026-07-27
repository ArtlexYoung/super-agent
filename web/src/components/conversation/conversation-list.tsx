import { useState } from "react"
import { Check, MessageSquarePlus, Pencil, Trash2, X } from "lucide-react"

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
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { Conversation } from "@/lib/types"
import { cn } from "@/lib/utils"

interface ConversationListProps {
  conversations: Conversation[]
  selectedConversationId: string | null
  busy: boolean
  onCreate: () => void
  onSelect: (conversationId: string) => void
  onRename: (conversationId: string, title: string) => void
  onDelete: (conversationId: string) => void
}

const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
})

export function ConversationList(props: ConversationListProps) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState("")

  function beginRename(conversation: Conversation): void {
    setEditingId(conversation.conversation_id)
    setEditingTitle(conversation.title || "未命名会话")
  }

  function submitRename(): void {
    if (editingId && editingTitle.trim()) {
      props.onRename(editingId, editingTitle.trim())
      setEditingId(null)
    }
  }

  return (
    <section className="conversation-list-panel">
      <header className="panel-header">
        <div>
          <p className="section-eyebrow">工作区</p>
          <h2 className="panel-title">对话</h2>
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              size="icon"
              onClick={props.onCreate}
              disabled={props.busy}
              aria-label="新建会话"
            >
              <MessageSquarePlus />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={6}>
            新建会话
          </TooltipContent>
        </Tooltip>
      </header>
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-1.5 p-2">
          {props.conversations.length === 0 ? (
            <div className="px-3 py-12 text-center text-sm text-muted-foreground">
              暂无会话
            </div>
          ) : null}
          {props.conversations.map((conversation) => {
            const selected =
              conversation.conversation_id === props.selectedConversationId
            const editing = conversation.conversation_id === editingId
            return (
              <div
                key={conversation.conversation_id}
                className={cn(
                  "group conversation-list-item",
                  selected && "conversation-list-item-selected"
                )}
              >
                {editing ? (
                  <div className="flex min-w-0 flex-1 items-center gap-1">
                    <Input
                      value={editingTitle}
                      autoFocus
                      aria-label="会话名称"
                      onChange={(event) => setEditingTitle(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") submitRename()
                        if (event.key === "Escape") setEditingId(null)
                      }}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      onClick={submitRename}
                      aria-label="确认重命名"
                    >
                      <Check />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => setEditingId(null)}
                      aria-label="取消重命名"
                    >
                      <X />
                    </Button>
                  </div>
                ) : (
                  <>
                    <button
                      type="button"
                      className="min-w-0 flex-1 text-left"
                      onClick={() =>
                        props.onSelect(conversation.conversation_id)
                      }
                    >
                      <span className="block truncate text-sm font-medium">
                        {conversation.title || "未命名会话"}
                      </span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {formatDate(conversation.updated_at)} ·{" "}
                        {conversation.messages.length}
                        条消息
                      </span>
                    </button>
                    <div className="conversation-item-actions">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-xs"
                            onClick={() => beginRename(conversation)}
                            aria-label="重命名会话"
                          >
                            <Pencil />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent side="top">重命名</TooltipContent>
                      </Tooltip>
                      <AlertDialog>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <AlertDialogTrigger asChild>
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon-xs"
                                className="text-muted-foreground hover:text-destructive"
                                aria-label="删除会话"
                              >
                                <Trash2 />
                              </Button>
                            </AlertDialogTrigger>
                          </TooltipTrigger>
                          <TooltipContent side="top">删除</TooltipContent>
                        </Tooltip>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>删除此会话？</AlertDialogTitle>
                            <AlertDialogDescription>
                              会话及其消息索引将从当前用户空间移除。
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>取消</AlertDialogCancel>
                            <AlertDialogAction
                              variant="destructive"
                              onClick={() =>
                                props.onDelete(conversation.conversation_id)
                              }
                            >
                              删除
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    </div>
                  </>
                )}
              </div>
            )
          })}
        </div>
      </ScrollArea>
    </section>
  )
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateFormatter.format(date)
}
