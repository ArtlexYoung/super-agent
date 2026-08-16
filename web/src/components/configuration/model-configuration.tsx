import { useState } from "react"
import {
  Bot,
  Check,
  CirclePlus,
  Pencil,
  Trash2,
} from "lucide-react"

import { ModelEditor } from "@/components/configuration/model-editor"
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
import type { ModelInput, ModelProfile } from "@/lib/types"

interface ModelConfigurationProps {
  models: ModelProfile[]
  busy: boolean
  onSave: (model: ModelInput) => Promise<void>
  onDelete: (name: string) => Promise<void>
}

export function ModelConfiguration(props: ModelConfigurationProps) {
  const [editing, setEditing] = useState<ModelInput | null>(null)

  function editModel(model: ModelProfile): void {
    setEditing(modelInputFromProfile(model))
  }

  async function submitModel(model: ModelInput): Promise<void> {
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
          <HelpTooltip label="模型连接、用途、权重和价格保存在通用 TOML；真实密钥只从环境变量读取。" />
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
        {props.models.map((model) => (
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
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  {model.description}
                </p>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  <span>{providerLabel(model.provider)}</span>
                  <span>{model.model}</span>
                  <span>{model.supports.join(" · ") || "text"}</span>
                  <span>权重 {model.weight}</span>
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
                  编辑
                </Button>
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
                        通用配置中的 {model.name} 将被移除；其他模型不受影响。
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
              </div>
            </article>
          ))}
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

function emptyModelInput(defaultModel: boolean): ModelInput {
  return {
    name: "",
    description: "",
    provider: "openai-compatible",
    model: "",
    base_url: "",
    api_key_env: "",
    supports: ["text"],
    purposes: ["answer"],
    weight: 1,
    default: defaultModel,
    input_cost_per_million: null,
    output_cost_per_million: null,
    cache_creation_cost_per_million: null,
    cache_read_cost_per_million: null,
    previous_name: "",
  }
}

function modelInputFromProfile(model: ModelProfile): ModelInput {
  return {
    name: model.name,
    description: model.description,
    provider: model.provider,
    model: model.model,
    base_url: model.base_url || "",
    api_key_env: model.api_key_env || "",
    supports: [...model.supports],
    purposes: [...model.purposes],
    weight: model.weight,
    default: model.default,
    input_cost_per_million: model.input_cost_per_million,
    output_cost_per_million: model.output_cost_per_million,
    cache_creation_cost_per_million: model.cache_creation_cost_per_million,
    cache_read_cost_per_million: model.cache_read_cost_per_million,
    previous_name: model.name,
  }
}

function providerLabel(provider: string): string {
  const labels: Record<string, string> = {
    "openai-compatible": "OpenAI 兼容",
    "anthropic-compatible": "Anthropic 兼容",
    mock: "本地 Mock",
  }
  return labels[provider] || provider
}
