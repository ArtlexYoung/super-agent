import { useId, useState } from "react"
import { ChevronDown, ServerCog } from "lucide-react"

import { HelpTooltip } from "@/components/shared/help-tooltip"
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
import type { ModelInput } from "@/lib/types"

interface ModelEditorProps {
  model: ModelInput | null
  busy: boolean
  onClose: () => void
  onSave: (model: ModelInput) => Promise<void>
}

export function ModelEditor(props: ModelEditorProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [draft, setDraft] = useState<ModelInput | null>(null)
  const model =
    draft?.previous_name === props.model?.previous_name && draft
      ? draft
      : props.model

  function update<Field extends keyof ModelInput>(
    field: Field,
    value: ModelInput[Field]
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
      <SheetContent className="w-screen gap-0 data-[side=right]:w-screen data-[side=right]:max-w-none data-[side=right]:sm:max-w-2xl">
        <SheetHeader className="border-b">
          <SheetTitle>
            {model?.previous_name ? "编辑模型" : "添加模型"}
          </SheetTitle>
          <SheetDescription>
            连接与调度信息写入通用配置，密钥值保留在运行环境中。
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
                  <div className="grid gap-4 sm:grid-cols-2">
                    <NumberInput
                      label="调度权重"
                      help="价格相近时优先选择权重更高的模型，必须大于 0。"
                      value={model.weight}
                      step="0.1"
                      onChange={(value) => update("weight", value || 1)}
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
                    <NumberInput
                      label="缓存创建成本 / 百万 token"
                      help="供应商创建提示缓存的单价；不支持或不计费时可留空。"
                      value={model.cache_creation_cost_per_million}
                      step="0.01"
                      onChange={(value) =>
                        update("cache_creation_cost_per_million", value)
                      }
                    />
                    <NumberInput
                      label="缓存读取成本 / 百万 token"
                      help="供应商读取提示缓存的单价；不支持或不计费时可留空。"
                      value={model.cache_read_cost_per_million}
                      step="0.01"
                      onChange={(value) =>
                        update("cache_read_cost_per_million", value)
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
      <div className="config-field-label">
        <span>{props.label}</span>
        <HelpTooltip label={props.help} />
      </div>
      <Switch
        checked={props.checked}
        aria-label={props.label}
        onCheckedChange={props.onChange}
      />
    </div>
  )
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

function featureLabel(feature: string): string {
  const labels: Record<string, string> = {
    text: "文本",
    tools: "工具",
    json: "JSON",
    vision: "视觉",
  }
  return labels[feature] || feature
}
