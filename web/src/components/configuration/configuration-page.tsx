import { useState } from "react"
import {
  Bot,
  Brain,
  Database,
  Save,
  Settings2,
  Sparkles,
} from "lucide-react"

import { MemoryConfiguration } from "@/components/configuration/memory-configuration"
import { ModelConfiguration } from "@/components/configuration/model-configuration"
import { SkillConfiguration } from "@/components/configuration/skill-configuration"
import { HelpTooltip } from "@/components/shared/help-tooltip"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import type { AgentConfiguration } from "@/lib/types"
import type { SuperAgentController } from "@/lib/use-super-agent"

interface ConfigurationPageProps {
  controller: SuperAgentController
}

export function ConfigurationPage({ controller }: ConfigurationPageProps) {
  const data = controller.bootstrap!
  const [draft, setDraft] = useState<AgentConfiguration>(data.agent)

  return (
    <div className="configuration-page">
      <header className="configuration-page-header">
        <div>
          <p className="section-eyebrow">{data.configuration_path}</p>
          <h1>配置</h1>
        </div>
        <Button
          type="button"
          disabled={controller.busy}
          onClick={() => void controller.saveConfiguration(draft)}
        >
          <Save />
          保存配置
        </Button>
      </header>
      <Tabs defaultValue="agent" className="min-h-0 flex-1 gap-0">
        <div className="configuration-tabs-bar">
          <TabsList variant="line">
            <TabsTrigger value="agent">
              <Settings2 /> Agent
            </TabsTrigger>
            <TabsTrigger value="skills">
              <Sparkles /> Skill
            </TabsTrigger>
            <TabsTrigger value="models">
              <Bot /> 模型
            </TabsTrigger>
            <TabsTrigger value="memory">
              <Brain /> 记忆
            </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="agent" className="configuration-scroll-area">
          <AgentConfigurationPanel
            configuration={draft}
            storage={data.storage}
            onChange={setDraft}
          />
        </TabsContent>
        <TabsContent value="skills" className="configuration-scroll-area">
          <SkillConfiguration
            skills={data.skills.filter((skill) => skill.type !== "model")}
            configuration={draft}
            onChange={setDraft}
          />
        </TabsContent>
        <TabsContent value="models" className="configuration-scroll-area">
          <ModelConfiguration
            models={data.models}
            skills={data.skills}
            busy={controller.busy}
            onSave={controller.saveModel}
            onDelete={controller.deleteModel}
          />
        </TabsContent>
        <TabsContent value="memory" className="configuration-scroll-area">
          <MemoryConfiguration
            memory={data.memory}
            busy={controller.busy}
            onForget={(itemId) => void controller.forgetMemoryItem(itemId)}
          />
        </TabsContent>
      </Tabs>
    </div>
  )
}

interface AgentConfigurationPanelProps {
  configuration: AgentConfiguration
  storage: { backend: string; path: string }
  onChange: (configuration: AgentConfiguration) => void
}

function AgentConfigurationPanel(props: AgentConfigurationPanelProps) {
  const config = props.configuration
  const maxDepthOptions = new Set([4, 8, 16, 32, 64])
  if (config.max_agent_chain_depth) {
    maxDepthOptions.add(config.max_agent_chain_depth)
  }

  return (
    <div className="configuration-sections">
      <section className="configuration-section">
        <div className="configuration-section-header">
          <div>
            <h2>Agent</h2>
            <p>身份与基础行为</p>
          </div>
          <HelpTooltip label="Agent 组合 Provider、Runtime、SkillRunner 和 Skill；修改名称会切换到新的 Agent 隔离空间。" />
        </div>
        <div className="configuration-form-grid">
          <ConfigurationField
            label="名称"
            help="用于运行记录、存储隔离和子 Agent 树中的稳定显示名称。"
          >
            <Input
              aria-label="名称"
              value={config.name}
              onChange={(event) =>
                props.onChange({ ...config, name: event.target.value })
              }
            />
          </ConfigurationField>
          <ConfigurationField
            label="系统提示"
            help="所有任务都会收到的最小基础提示；具体行为优先拆入可渐进披露的 Skill。"
            wide
          >
            <Textarea
              aria-label="系统提示"
              value={config.system}
              className="min-h-28 resize-y"
              onChange={(event) =>
                props.onChange({ ...config, system: event.target.value })
              }
            />
          </ConfigurationField>
          <ConfigurationField
            label="Agent 链最大深度"
            help="运行前检查主从链路并提示超深；无限表示不设置机械深度限制。"
          >
            <Select
              value={config.max_agent_chain_depth?.toString() || "unlimited"}
              onValueChange={(value) =>
                props.onChange({
                  ...config,
                  max_agent_chain_depth:
                    value === "unlimited" ? null : Number(value),
                })
              }
            >
              <SelectTrigger className="w-full" aria-label="Agent 链最大深度">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="unlimited">无限</SelectItem>
                {[...maxDepthOptions]
                  .sort((left, right) => left - right)
                  .map((depth) => (
                    <SelectItem key={depth} value={depth.toString()}>
                      {depth} 层
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </ConfigurationField>
        </div>
      </section>

      <section className="configuration-section">
        <div className="configuration-section-header">
          <div>
            <h2>自动选择</h2>
            <p>零配置默认项</p>
          </div>
          <HelpTooltip label="Runtime 使用统一的渐进式披露核心；Skill 可逐项启用、禁用或固定。" />
        </div>
        <div className="feature-summary-row">
          <div className="feature-summary-icon">
            <Sparkles />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">渐进式 Skill</p>
            <p className="text-sm text-muted-foreground">
              先索引、后 manifest、命中后才读取内容与资源
            </p>
          </div>
          <Badge variant="secondary">已启用</Badge>
        </div>
      </section>

      <section className="configuration-section">
        <div className="configuration-section-header">
          <div>
            <h2>存储</h2>
            <p>当前 Runtime 后端</p>
          </div>
          <HelpTooltip label="存储方式在 Agent 创建时确定；切换 JSONL、SQLite、MySQL 或 PostgreSQL 后需重启 Runtime。" />
        </div>
        <div className="storage-summary">
          <div className="feature-summary-icon">
            <Database />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium">
                {storageLabel(props.storage.backend)}
              </p>
              <Badge variant="outline">当前</Badge>
            </div>
            <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
              {props.storage.path}
            </p>
          </div>
          <Badge variant="secondary">按用户隔离</Badge>
        </div>
      </section>
    </div>
  )
}

function ConfigurationField(props: {
  label: string
  help: string
  wide?: boolean
  children: React.ReactNode
}) {
  return (
    <div className={props.wide ? "config-field sm:col-span-2" : "config-field"}>
      <div className="config-field-label">
        <span>{props.label}</span>
        <HelpTooltip label={props.help} />
      </div>
      {props.children}
    </div>
  )
}

function storageLabel(backend: string): string {
  const labels: Record<string, string> = {
    jsonl: "本地 JSONL",
    sqlite: "SQLite",
    mysql: "MySQL",
    postgresql: "PostgreSQL",
  }
  return labels[backend] || backend
}
