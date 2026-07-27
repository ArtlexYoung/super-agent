import { Check, Pin, Sparkles } from "lucide-react"

import { HelpTooltip } from "@/components/shared/help-tooltip"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Progress } from "@/components/ui/progress"
import { Switch } from "@/components/ui/switch"
import type { AgentConfiguration, SkillState } from "@/lib/types"
import { cn } from "@/lib/utils"

interface SkillConfigurationProps {
  skills: SkillState[]
  configuration: AgentConfiguration
  onChange: (configuration: AgentConfiguration) => void
}

const groups = [
  {
    capability: "prompt",
    title: "提示 Skill",
    help: "为模型渐进披露的提示内容，可按任务自动选择，也可固定使用。",
  },
  {
    capability: "mcp",
    title: "MCP",
    help: "声明外部工具连接；实际调用仍需经过 Runtime Safety。",
  },
  {
    capability: "memory",
    title: "记忆",
    help: "选择启用的记忆机制，并指定 Agent 默认使用的 memory Skill。",
  },
  {
    capability: "workflow",
    title: "工作流",
    help: "工作流只承载执行策略和结束条件，Runtime 负责实际调度。",
  },
  {
    capability: "planner",
    title: "规划",
    help: "复杂任务按需加载 Planner Skill；简单任务保持直接执行。",
  },
  {
    capability: "capability",
    title: "执行能力",
    help: "由应用显式注册执行器的能力 Skill，声明内容本身不会作为代码运行。",
  },
]

export function SkillConfiguration(props: SkillConfigurationProps) {
  return (
    <div className="configuration-sections">
      {groups.map((group) => {
        const skills = props.skills.filter(
          (skill) => skill.capability === group.capability
        )
        return (
          <section key={group.capability} className="configuration-section">
            <div className="configuration-section-header">
              <div>
                <h2>{group.title}</h2>
                <p>{skills.length} 个可用项</p>
              </div>
              <HelpTooltip label={group.help} />
            </div>
            {skills.length ? (
              <div className="skill-list">
                {skills.map((skill) => (
                  <SkillRow
                    key={skill.key}
                    skill={skill}
                    configuration={props.configuration}
                    onChange={props.onChange}
                  />
                ))}
              </div>
            ) : (
              <p className="empty-configuration-row">暂无此类 Skill</p>
            )}
          </section>
        )
      })}
    </div>
  )
}

interface SkillRowProps {
  skill: SkillState
  configuration: AgentConfiguration
  onChange: (configuration: AgentConfiguration) => void
}

function SkillRow({ skill, configuration, onChange }: SkillRowProps) {
  const defaultField = defaultConfigurationField(skill.capability)
  const isDefault = defaultField
    ? configuration[defaultField] === skill.name
    : false
  const canPin = !defaultField && skill.capability !== "capability"
  const enabled =
    !configuration.disable_names.includes(skill.key) &&
    !configuration.disable_names.includes(skill.name)
  const selected =
    configuration.skills.includes(skill.key) ||
    configuration.skills.includes(skill.name)

  function setEnabled(nextEnabled: boolean): void {
    const withoutSkill = configuration.disable_names.filter(
      (item) => item !== skill.key && item !== skill.name
    )
    onChange({
      ...configuration,
      disable_names: nextEnabled ? withoutSkill : [...withoutSkill, skill.key],
    })
  }

  function setSelected(nextSelected: boolean): void {
    const withoutSkill = configuration.skills.filter(
      (item) => item !== skill.key && item !== skill.name
    )
    onChange({
      ...configuration,
      skills: nextSelected ? [...withoutSkill, skill.key] : withoutSkill,
    })
  }

  function setDefault(): void {
    if (defaultField) {
      onChange({ ...configuration, [defaultField]: skill.name })
    }
  }

  return (
    <div className={cn("skill-row", !enabled && "skill-row-disabled")}>
      <div className="skill-row-main">
        <div className="skill-row-heading">
          <span className="skill-name">{skill.name}</span>
          <Badge variant="outline">v{skill.version}</Badge>
          {isDefault ? (
            <Badge className="bg-emerald-600 text-white">
              <Check /> 默认
            </Badge>
          ) : null}
          {skill.agent_created ? (
            <Badge variant="secondary">
              <Sparkles /> Agent 创建
            </Badge>
          ) : null}
        </div>
        <p className="skill-description">{skill.description}</p>
        <div className="skill-freshness">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">保鲜度</span>
            <span className="font-medium tabular-nums">
              {skill.freshness.toFixed(0)}
            </span>
          </div>
          <Progress
            value={skill.freshness}
            className={cn(
              skill.freshness < 45 &&
                "[&_[data-slot=progress-indicator]]:bg-amber-500",
              skill.freshness >= 75 &&
                "[&_[data-slot=progress-indicator]]:bg-emerald-600"
            )}
          />
          <span className="text-[11px] text-muted-foreground">
            调用 {skill.call_count} 次 · 成功 {skill.success_count} 次
          </span>
        </div>
      </div>
      <div className="skill-row-actions">
        {defaultField && !isDefault ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!enabled}
            onClick={setDefault}
          >
            <Pin />
            设为默认
          </Button>
        ) : null}
        {canPin ? (
          <label className="skill-pin-control">
            <Checkbox
              checked={selected}
              disabled={!enabled}
              onCheckedChange={(checked) => setSelected(checked === true)}
            />
            固定使用
            <HelpTooltip label="固定使用后，每次任务都会加载该 Skill；未固定时由渐进式披露自动选择。" />
          </label>
        ) : null}
        <div className="skill-enable-control">
          <span>{enabled ? "已启用" : "已禁用"}</span>
          <Switch
            checked={enabled}
            disabled={isDefault}
            onCheckedChange={setEnabled}
            aria-label={`${enabled ? "禁用" : "启用"} ${skill.name}`}
          />
          {isDefault ? (
            <HelpTooltip label="默认项不能直接禁用，请先将同类其他 Skill 设为默认。" />
          ) : null}
        </div>
      </div>
    </div>
  )
}

function defaultConfigurationField(
  capability: string
): "memory" | "workflow" | null {
  if (capability === "memory") return "memory"
  if (capability === "workflow") return "workflow"
  return null
}
