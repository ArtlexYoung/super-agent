import { Activity, BrainCircuit, Clock3, Coins, Sparkles } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import type { RunInsight } from "@/lib/types"

interface RunInsightSheetProps {
  insight: RunInsight | null
  loading: boolean
  onClose: () => void
}

export function RunInsightSheet(props: RunInsightSheetProps) {
  const open = props.loading || props.insight !== null
  return (
    <Sheet
      open={open}
      onOpenChange={(nextOpen) => !nextOpen && props.onClose()}
    >
      <SheetContent className="w-full data-[side=right]:sm:max-w-xl">
        <SheetHeader className="border-b">
          <SheetTitle>运行详情</SheetTitle>
          <SheetDescription>
            Runtime 的调度、模型、Skill 与进化证据
          </SheetDescription>
        </SheetHeader>
        <ScrollArea className="min-h-0 flex-1">
          {props.loading ? <RunInsightSkeleton /> : null}
          {props.insight ? <RunInsightContent insight={props.insight} /> : null}
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}

function RunInsightContent({ insight }: { insight: RunInsight }) {
  const snapshot = insight.snapshot
  return (
    <div className="space-y-7 p-5">
      <section>
        <SectionTitle icon={Activity} title="运行" />
        <div className="insight-summary-grid">
          <Metric label="状态" value={statusLabel(snapshot.status)} />
          <Metric label="工作流" value={snapshot.workflow || "direct"} />
          <Metric label="事件" value={String(snapshot.event_count)} />
          <Metric label="结束原因" value={snapshot.stop_reason || "-"} />
        </div>
        <p className="mt-3 rounded-md bg-muted/60 p-3 text-sm leading-6">
          {contentSummary(snapshot.prompt)}
        </p>
      </section>

      <section>
        <SectionTitle icon={Clock3} title="模型调用" />
        {insight.model_calls.length ? (
          <div className="space-y-2">
            {insight.model_calls.map((call, index) => (
              <div key={index} className="insight-row">
                <span className="insight-index">{index + 1}</span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">
                    {modelCallTitle(call)}
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {modelCallDetail(call)}
                  </p>
                </div>
                <Badge variant="secondary">
                  {numberValue(call.turn) > 0
                    ? `第 ${numberValue(call.turn)} 轮`
                    : "调度"}
                </Badge>
              </div>
            ))}
          </div>
        ) : (
          <EmptyInsight label="暂无模型调用记录" />
        )}
      </section>

      <section>
        <SectionTitle icon={Coins} title="模型用量" />
        {insight.model_usage.length ? (
          <div className="space-y-2">
            {insight.model_usage.map((usage, index) => (
              <div key={index} className="insight-row">
                <span className="insight-index">{index + 1}</span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">
                    第 {numberValue(usage.turn) || index + 1} 轮
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {modelUsageDetail(usage)}
                  </p>
                </div>
                <Badge variant="outline">{modelCostLabel(usage)}</Badge>
              </div>
            ))}
          </div>
        ) : (
          <EmptyInsight label="模型未返回用量数据" />
        )}
      </section>

      <section>
        <SectionTitle icon={BrainCircuit} title="Skill 保鲜度" />
        {insight.skill_freshness.length ? (
          <div className="space-y-3">
            {insight.skill_freshness.map((skill, index) => {
              const freshness = numberValue(skill.freshness)
              return (
                <div key={index}>
                  <div className="mb-1.5 flex items-center justify-between gap-3 text-sm">
                    <span className="truncate font-medium">
                      {textValue(skill.skill) ||
                        textValue(skill.skill_key) ||
                        textValue(skill.key) ||
                        "Skill"}
                    </span>
                    <span className="text-muted-foreground tabular-nums">
                      {freshness.toFixed(0)}
                    </span>
                  </div>
                  <Progress value={freshness} />
                </div>
              )
            })}
          </div>
        ) : (
          <EmptyInsight label="本次运行没有 Skill 评价" />
        )}
      </section>

      <section>
        <SectionTitle icon={Sparkles} title="进化" />
        {insight.evolution.length ? (
          <div className="space-y-2">
            {insight.evolution.map((item, index) => (
              <div key={index} className="insight-row">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">
                    {textValue(item.skill_key) ||
                      textValue(item.target_key) ||
                      "Skill"}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {textValue(item.reason) || "由运行证据触发"}
                  </p>
                </div>
                <Badge variant="secondary">
                  {textValue(item.status) || "observed"}
                </Badge>
              </div>
            ))}
          </div>
        ) : (
          <EmptyInsight label="本次运行未触发进化" />
        )}
      </section>
    </div>
  )
}

function SectionTitle({
  icon: Icon,
  title,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
}) {
  return (
    <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
      <Icon className="size-4 text-primary" />
      {title}
    </h3>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-l-2 border-primary/30 pl-2">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-0.5 truncate text-sm font-medium">{value}</p>
    </div>
  )
}

function EmptyInsight({ label }: { label: string }) {
  return (
    <p className="rounded-md bg-muted/50 px-3 py-4 text-sm text-muted-foreground">
      {label}
    </p>
  )
}

function RunInsightSkeleton() {
  return (
    <div className="space-y-4 p-5">
      <Skeleton className="h-5 w-32" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-5 w-28" />
      <Skeleton className="h-40 w-full" />
    </div>
  )
}

function textValue(value: unknown): string {
  return typeof value === "string" ? value : ""
}

function contentSummary(value: unknown): string {
  if (typeof value === "string") return value
  if (typeof value !== "object" || value === null) return "未记录输入"
  const redacted = value as Record<string, unknown>
  const characters = numberValue(redacted.characters)
  const digest = textValue(redacted.sha256)
  return `内容已脱敏 · ${characters} 字符${digest ? ` · ${digest.slice(0, 12)}` : ""}`
}

function modelCallTitle(call: Record<string, unknown>): string {
  const profile = textValue(call.profile)
  if (profile) return profile
  const status = textValue(call.status)
  if (status === "model_fallback") return "模型回退"
  const eventType = textValue(call.event_type)
  if (eventType === "model.call.started") return "模型请求开始"
  if (eventType === "model.call.completed") return "模型请求完成"
  return status || eventType || "模型事件"
}

function modelCallDetail(call: Record<string, unknown>): string {
  const details: string[] = []
  if ("message_count" in call) details.push(`${numberValue(call.message_count)} 条消息`)
  if ("text_characters" in call) details.push(`${numberValue(call.text_characters)} 字符`)
  if ("tool_call_count" in call) details.push(`${numberValue(call.tool_call_count)} 个工具请求`)
  if ("candidate_count" in call) details.push(`${numberValue(call.candidate_count)} 个候选模型`)
  return details.join(" · ") || "Runtime 调度事件"
}

function modelUsageDetail(usage: Record<string, unknown>): string {
  const details = [
    `输入 ${numberValue(usage.input_tokens)}`,
    `输出 ${numberValue(usage.output_tokens)}`,
  ]
  if ("cache_creation_tokens" in usage) {
    details.push(`缓存创建 ${numberValue(usage.cache_creation_tokens)}`)
  }
  if ("cache_read_tokens" in usage) {
    details.push(`缓存读取 ${numberValue(usage.cache_read_tokens)}`)
  }
  return details.join(" · ")
}

function modelCostLabel(usage: Record<string, unknown>): string {
  return "estimated_cost" in usage
    ? `成本 ${numberValue(usage.estimated_cost).toFixed(6)}`
    : "已记录"
}

function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    completed: "已完成",
    failed: "失败",
    running: "运行中",
  }
  return labels[status] || status
}
