import { useState } from "react"
import {
  Bot,
  ChevronRight,
  CircleCheck,
  CircleDashed,
  GitBranch,
  Sparkles,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { RunSnapshot, SubagentNode } from "@/lib/types"
import { cn } from "@/lib/utils"

interface RunTreeProps {
  agentName: string
  conversationId: string | null
  runs: RunSnapshot[]
  subagents: SubagentNode[]
  onOpenRun: (runId: string) => void
}

export function RunTree(props: RunTreeProps) {
  const mainRuns = runsForConversation(props.runs, props.conversationId)
  return (
    <section className="run-tree-panel">
      <header className="panel-header border-b">
        <div>
          <p className="section-eyebrow">实时路径</p>
          <h2 className="panel-title">Agent 运行树</h2>
        </div>
        <GitBranch className="size-4 text-muted-foreground" />
      </header>
      <ScrollArea className="min-h-0 flex-1">
        <div className="p-3">
          <AgentTreeNode
            name={props.agentName}
            agentName={props.agentName}
            description="主 Agent"
            createdByAgent={false}
            runs={mainRuns}
            children={props.subagents}
            conversationId={props.conversationId}
            level={0}
            onOpenRun={props.onOpenRun}
          />
        </div>
      </ScrollArea>
    </section>
  )
}

interface AgentTreeNodeProps {
  name: string
  agentName: string
  description: string
  createdByAgent: boolean
  runs: RunSnapshot[]
  children: SubagentNode[]
  conversationId: string | null
  level: number
  onOpenRun: (runId: string) => void
}

function AgentTreeNode(props: AgentTreeNodeProps) {
  const [open, setOpen] = useState(true)
  const hasContent = props.runs.length > 0 || props.children.length > 0
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div
        className="agent-tree-node"
        style={{ "--tree-level": props.level } as React.CSSProperties}
      >
        <CollapsibleTrigger asChild disabled={!hasContent}>
          <Button type="button" variant="ghost" className="agent-tree-trigger">
            <ChevronRight
              className={cn("tree-chevron", open && "tree-chevron-open")}
            />
            <span className="agent-tree-icon">
              {props.createdByAgent ? <Sparkles /> : <Bot />}
            </span>
            <span className="min-w-0 flex-1 text-left">
              <span className="block truncate text-sm font-medium">
                {props.name}
              </span>
              <span className="block truncate text-xs text-muted-foreground">
                {props.description || props.agentName}
              </span>
            </span>
            {props.createdByAgent ? (
              <Badge variant="secondary" className="px-1.5">
                自建
              </Badge>
            ) : null}
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="agent-tree-children">
            {props.runs.map((run) => (
              <button
                type="button"
                key={run.run_id}
                className="run-tree-item"
                onClick={() => props.onOpenRun(run.run_id)}
              >
                {run.status === "completed" ? (
                  <CircleCheck className="size-3.5 text-emerald-600" />
                ) : (
                  <CircleDashed className="size-3.5 text-amber-600" />
                )}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium">
                    {runPromptLabel(run.prompt)}
                  </span>
                  <span className="block truncate text-[11px] text-muted-foreground">
                    {run.workflow || "direct"} · {shortRunId(run.run_id)}
                  </span>
                </span>
              </button>
            ))}
            {props.children.map((child) => (
              <AgentTreeNode
                key={child.path.join("/")}
                name={child.name}
                agentName={child.agent_name}
                description={child.description}
                createdByAgent={child.created_by_agent}
                runs={runsForConversation(child.runs, props.conversationId)}
                children={child.children}
                conversationId={props.conversationId}
                level={props.level + 1}
                onOpenRun={props.onOpenRun}
              />
            ))}
            {!hasContent ? (
              <p className="px-8 py-2 text-xs text-muted-foreground">
                暂无运行
              </p>
            ) : null}
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  )
}

function runsForConversation(
  runs: RunSnapshot[],
  conversationId: string | null
): RunSnapshot[] {
  if (!conversationId) {
    return []
  }
  return runs.filter((run) => run.conversation_id === conversationId)
}

function shortRunId(runId: string): string {
  return runId.length > 10 ? runId.slice(0, 10) : runId
}

function runPromptLabel(value: unknown): string {
  if (typeof value === "string" && value.trim()) return value
  if (typeof value !== "object" || value === null) return "运行"
  const characters = (value as Record<string, unknown>).characters
  return typeof characters === "number" ? `已脱敏输入 · ${characters} 字符` : "已脱敏输入"
}
