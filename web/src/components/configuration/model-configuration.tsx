import { useId, useState } from "react"
import {
  Bot,
  Check,
  ChevronDown,
  CirclePlus,
  Pencil,
  ServerCog,
  Trash2,
} from "lucide-react"

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
import { Checkbox } from "@/components/ui/checkbox"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Switch } from "@/components/ui/switch"
import type { ModelProfile, ModelSkillInput, SkillState } from "@/lib/types"

interface ModelConfigurationProps {
  models: ModelProfile[]
  skills: SkillState[]
  busy: boolean
  onSave: (model: ModelSkillInput) => Promise<void>
  onDelete: (name: string) => Promise<void>
}

export function ModelConfiguration(props: ModelConfigurationProps) {
  const [editing, setEditing] = useState<ModelSkillInput | null>(null)

  function editModel(model: ModelProfile): void {
    setEditing(modelInputFromProfile(model))
  }

  async function submitModel(model: ModelSkillInput): Promise<void> {
    await props.onSave(model)
    setEditing(null)
  }

  return (
    <section className="configuration-section">
      <div className="configuration-section-header">
        <div>
          <h2>模型</h2>
          <p>{props.models.length} 个可调度模型</p>
        </div>
        <div className="flex items-center gap-1">
          <HelpTooltip label="模型也是 Skill，名称、连接和路由特征会保存到项目 Skill 目录；真实密钥只从环境变量读取。" />
          <Button
            type="button"
            onClick={() =>
              setEditing(emptyModelInput(props.models.length === 0))
            }
          >
            <CirclePlus />
            添加模型
          </Button>
        </div>
      </div>
      <div className="model-list">
        {props.models.map((model) => {
          const freshness = props.skills.find(
            (skill) => skill.key === model.skill_key
          )?.freshness
          return (
            <article key={model.key} className="model-row">
              <div className="model-icon">
                <Bot />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold">{model.name}</h3>
                  {model.default ? (
                    <Badge className="bg-emerald-600 text-white">
                      <Check /> 默认
                    </Badge>
                  ) : null}
                  <Badge variant={model.ready ? "secondary" : "destructive"}>
                    {model.ready ? "就绪" : "缺少密钥"}
                  </Badge>
                  {freshness !== undefined ? (
                    <Badge variant="outline">
                      保鲜度 {freshness.toFixed(0)}
                    </Badge>
                  ) : null}
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  {model.description}
                </p>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  <span>{providerLabel(model.provider)}</span>
                  <span>{model.model}</span>
                  <span>{model.supports.join(" · ") || "text"}</span>
                  <span>
                    {model.skill_key ? `Skill ${model.version}` : "自动发现"}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-1">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => editModel(model)}
                >
                  <Pencil />
                  {model.skill_key ? "编辑" : "保存配置"}
                </Button>
                {model.skill_key ? (
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        disabled={props.busy}
                        className="text-muted-foreground hover:text-destructive"
                        aria-label={`删除模型 ${model.name}`}
                      >
                        <Trash2 />
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>删除模型配置？</AlertDialogTitle>
                        <AlertDialogDescription>
                          model:{model.name} 的 Skill
                          目录将被移除；其他模型不受影响。
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>取消</AlertDialogCancel>
                        <AlertDialogAction
                          variant="destructive"
                          onClick={() => void props.onDelete(model.name)}
                        >
                          删除
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                ) : null}
              </div>
            </article>
          )
        })}
      </div>
      <ModelEditor
        key={
          editing ? editing.previous_name || editing.name || "new" : "closed"
        }
        model={editing}
        busy={props.busy}
        onClose={() => setEditing(null)}
        onSave={submitModel}
      />
    </section>
  )
}

interface ModelEditorProps {
  model: ModelSkillInput | null
  busy: boolean
  onClose: () => void
  onSave: (model: ModelSkillInput) => Promise<void>
}

function ModelEditor(props: ModelEditorProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [draft, setDraft] = useState<ModelSkillInput | null>(null)
  const model =
    draft?.previous_name === props.model?.previous_name && draft
      ? draft
      : props.model

  function update<Field extends keyof ModelSkillInput>(
    field: Field,
    value: ModelSkillInput[Field]
  ): void {
    if (model) {
      setDraft({ ...model, [field]: value })
    }
  }

  function close(): void {
    setDraft(null)
    setAdvancedOpen(false)
    props.onClose()
  }

  async function submit(event: React.FormEvent): Promise<void> {
    event.preventDefault()
    if (!model) return
    await props.onSave(model)
    setDraft(null)
    setAdvancedOpen(false)
  }

  return (
    <Sheet
      open={props.model !== null}
      onOpenChange={(open) => !open && close()}
    >
      <SheetContent className="w-full gap-0 data-[side=right]:sm:max-w-2xl">
        <SheetHeader className="border-b">
          <SheetTitle>
            {model?.previous_name ? "编辑模型" : "添加模型"}
          </SheetTitle>
          <SheetDescription>
            连接信息写入 model Skill，密钥值保留在运行环境中。
          </SheetDescription>
        </SheetHeader>
        {model ? (
          <form className="flex min-h-0 flex-1 flex-col" onSubmit={submit}>
            <div className="model-editor-fields">
              <div className="grid gap-4 sm:grid-cols-2">
                <ConfigInput
                  label="配置名称"
                  help="模型 Skill 的唯一名称，仅支持小写字母、数字、短横线和下划线。"
                  value={model.name}
                  required
                  onChange={(value) => update("name", value.toLowerCase())}
                />
                <ConfigSelect
                  label="供应商"
                  help="Provider 只负责连接模型智能，Runtime 仍统一调度所有能力。"
                  value={model.provider}
                  options={[
                    ["openai-compatible", "OpenAI 兼容"],
                    ["anthropic-compatible", "Anthropic 兼容"],
                    ["mock", "本地 Mock"],
                  ]}
                  onChange={(value) => update("provider", value)}
                />
              </div>
              <ConfigInput
                label="显示说明"
                help="用于自动调度时理解此模型适合处理的任务。"
                value={model.description}
                required
                onChange={(value) => update("description", value)}
              />
              <div className="grid gap-4 sm:grid-cols-2">
                <ConfigInput
                  label="模型名称"
                  help="发送给模型供应商的实际 model 标识。"
                  value={model.model}
                  required
                  onChange={(value) => update("model", value)}
                />
                <ConfigInput
                  label="服务地址"
                  help="OpenAI 兼容接口的 base URL；使用供应商默认地址时可留空。"
                  value={model.base_url}
                  placeholder="https://api.example.com/v1"
                  onChange={(value) => update("base_url", value)}
                />
              </div>
              <ConfigInput
                label="密钥环境变量"
                help="这里只保存环境变量名，例如 OPENAI_API_KEY；不会保存真实密钥。"
                value={model.api_key_env}
                placeholder="OPENAI_API_KEY"
                onChange={(value) => update("api_key_env", value)}
              />
              <div className="config-field">
                <div className="config-field-label">
                  <label>支持能力</label>
                  <HelpTooltip label="调度器只会把任务分配给声明支持所需特性的模型。" />
                </div>
                <div className="flex flex-wrap gap-4">
                  {["text", "tools", "json", "vision"].map((feature) => (
                    <label key={feature} className="checkbox-label">
                      <Checkbox
                        checked={model.supports.includes(feature)}
                        onCheckedChange={(checked) =>
                          update(
                            "supports",
                            toggleListValue(
                              model.supports,
                              feature,
                              checked === true
                            )
                          )
                        }
                      />
                      {featureLabel(feature)}
                    </label>
                  ))}
                </div>
              </div>
              <ConfigInput
                label="擅长任务"
                help="使用逗号分隔任务用途，例如 answer、summary、coding。"
                value={model.purposes.join(", ")}
                placeholder="answer, summary"
                onChange={(value) => update("purposes", commaSeparated(value))}
              />
              <div className="model-switch-grid">
                <SwitchField
                  label="设为默认"
                  help="没有明确路由证据时优先使用此模型。"
                  checked={model.default}
                  onChange={(value) => update("default", value)}
                />
                <SwitchField
                  label="允许 Agent 优化"
                  help="允许进化闭环更新此 model Skill 的路由说明和参数。"
                  checked={model.agent_can_update}
                  onChange={(value) => update("agent_can_update", value)}
                />
                <SwitchField
                  label="允许更新连接"
                  help="默认关闭，避免 Agent 改写供应商、地址和密钥环境变量。"
                  checked={model.agent_can_update_connection}
                  onChange={(value) =>
                    update("agent_can_update_connection", value)
                  }
                />
              </div>
              <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
                <CollapsibleTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    className="w-full justify-between"
                  >
                    <span className="inline-flex items-center gap-2">
                      <ServerCog />
                      路由参数
                    </span>
                    <ChevronDown
                      className={
                        advancedOpen
                          ? "rotate-180 transition-transform"
                          : "transition-transform"
                      }
                    />
                  </Button>
                </CollapsibleTrigger>
                <CollapsibleContent className="space-y-4 pt-4">
                  <ConfigInput
                    label="优势标签"
                    help="描述模型的相对优势，使用逗号分隔。"
                    value={model.strengths.join(", ")}
                    onChange={(value) =>
                      update("strengths", commaSeparated(value))
                    }
                  />
                  <ConfigInput
                    label="触发词"
                    help="任务文本命中这些词时，提高该模型的初始匹配度。"
                    value={model.triggers.join(", ")}
                    onChange={(value) =>
                      update("triggers", commaSeparated(value))
                    }
                  />
                  <div className="grid gap-4 sm:grid-cols-2">
                    <NumberInput
                      label="初始质量"
                      help="冷启动路由质量，范围 0 到 1。"
                      value={model.quality_score}
                      step="0.05"
                      onChange={(value) => update("quality_score", value)}
                    />
                    <NumberInput
                      label="预计延迟（ms）"
                      help="没有真实运行证据时使用的延迟估计。"
                      value={model.expected_latency_ms}
                      step="1"
                      onChange={(value) => update("expected_latency_ms", value)}
                    />
                    <NumberInput
                      label="输入成本 / 百万 token"
                      help="用于成本感知调度；不计费时可留空。"
                      value={model.input_cost_per_million}
                      step="0.01"
                      onChange={(value) =>
                        update("input_cost_per_million", value)
                      }
                    />
                    <NumberInput
                      label="输出成本 / 百万 token"
                      help="用于成本感知调度；不计费时可留空。"
                      value={model.output_cost_per_million}
                      step="0.01"
                      onChange={(value) =>
                        update("output_cost_per_million", value)
                      }
                    />
                  </div>
                </CollapsibleContent>
              </Collapsible>
            </div>
            <SheetFooter className="border-t bg-background">
              <div className="flex justify-end gap-2">
                <Button type="button" variant="outline" onClick={close}>
                  取消
                </Button>
                <Button
                  type="submit"
                  disabled={
                    props.busy ||
                    !model.name ||
                    !model.model ||
                    !model.description
                  }
                >
                  保存模型
                </Button>
              </div>
            </SheetFooter>
          </form>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}

interface ConfigInputProps {
  label: string
  help: string
  value: string
  required?: boolean
  placeholder?: string
  onChange: (value: string) => void
}

function ConfigInput(props: ConfigInputProps) {
  const inputId = useId()
  return (
    <div className="config-field">
      <div className="config-field-label">
        <label htmlFor={inputId}>{props.label}</label>
        <HelpTooltip label={props.help} />
      </div>
      <Input
        id={inputId}
        value={props.value}
        required={props.required}
        placeholder={props.placeholder}
        onChange={(event) => props.onChange(event.target.value)}
      />
    </div>
  )
}

interface NumberInputProps {
  label: string
  help: string
  value: number | null
  step: string
  onChange: (value: number | null) => void
}

function NumberInput(props: NumberInputProps) {
  const inputId = useId()
  return (
    <div className="config-field">
      <div className="config-field-label">
        <label htmlFor={inputId}>{props.label}</label>
        <HelpTooltip label={props.help} />
      </div>
      <Input
        id={inputId}
        type="number"
        min="0"
        step={props.step}
        value={props.value ?? ""}
        onChange={(event) =>
          props.onChange(event.target.value ? Number(event.target.value) : null)
        }
      />
    </div>
  )
}

interface ConfigSelectProps {
  label: string
  help: string
  value: string
  options: Array<[string, string]>
  onChange: (value: string) => void
}

function ConfigSelect(props: ConfigSelectProps) {
  const triggerId = useId()
  return (
    <div className="config-field">
      <div className="config-field-label">
        <label htmlFor={triggerId}>{props.label}</label>
        <HelpTooltip label={props.help} />
      </div>
      <Select value={props.value} onValueChange={props.onChange}>
        <SelectTrigger id={triggerId} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {props.options.map(([value, label]) => (
            <SelectItem key={value} value={value}>
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

function SwitchField(props: {
  label: string
  help: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <div className="model-switch-field">
      <div>
        <div className="config-field-label">
          <span>{props.label}</span>
          <HelpTooltip label={props.help} />
        </div>
      </div>
      <Switch
        checked={props.checked}
        aria-label={props.label}
        onCheckedChange={props.onChange}
      />
    </div>
  )
}

function emptyModelInput(defaultModel: boolean): ModelSkillInput {
  return {
    name: "",
    description: "",
    provider: "openai-compatible",
    model: "",
    base_url: "",
    api_key_env: "",
    supports: ["text"],
    purposes: ["answer"],
    strengths: [],
    triggers: [],
    default: defaultModel,
    agent_can_update: false,
    agent_can_update_connection: false,
    quality_score: 0.7,
    expected_latency_ms: 1000,
    input_cost_per_million: null,
    output_cost_per_million: null,
    previous_name: "",
  }
}

function modelInputFromProfile(model: ModelProfile): ModelSkillInput {
  return {
    name: model.name,
    description: model.description,
    provider: model.provider,
    model: model.model,
    base_url: model.base_url || "",
    api_key_env: model.api_key_env || "",
    supports: [...model.supports],
    purposes: [...model.purposes],
    strengths: [...model.strengths],
    triggers: [...model.triggers],
    default: model.default,
    agent_can_update: model.agent_can_update,
    agent_can_update_connection: model.agent_can_update_connection,
    quality_score: model.quality_score,
    expected_latency_ms: model.expected_latency_ms,
    input_cost_per_million: model.input_cost_per_million,
    output_cost_per_million: model.output_cost_per_million,
    previous_name: model.skill_key ? model.name : "",
  }
}

function toggleListValue(
  values: string[],
  value: string,
  enabled: boolean
): string[] {
  const remaining = values.filter((item) => item !== value)
  return enabled ? [...remaining, value] : remaining
}

function commaSeparated(value: string): string[] {
  return [
    ...new Set(
      value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean)
    ),
  ]
}

function providerLabel(provider: string): string {
  const labels: Record<string, string> = {
    "openai-compatible": "OpenAI 兼容",
    "anthropic-compatible": "Anthropic 兼容",
    mock: "本地 Mock",
  }
  return labels[provider] || provider
}

function featureLabel(feature: string): string {
  const labels: Record<string, string> = {
    text: "文本",
    tools: "工具",
    json: "JSON",
    vision: "视觉",
  }
  return labels[feature] || feature
}
