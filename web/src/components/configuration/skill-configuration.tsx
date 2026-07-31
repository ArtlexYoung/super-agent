import { Sparkles } from "lucide-react"

import { HelpTooltip } from "@/components/shared/help-tooltip"
import { Badge } from "@/components/ui/badge"
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

const knownGroups = [
  {
    type: "task",
    title: "任务 Skill",
    help: "任务 Skill 同时提供模型指令和执行方式；每次运行最多使用一个。",
  },
  {
    type: "prompt",
    title: "提示 Skill",
    help: "为模型渐进披露的提示内容，可按任务自动选择，也可固定使用。",
  },
  {
    type: "mcp",
    title: "MCP",
    help: "声明外部工具连接；实际调用仍需经过 Runtime 的动作规则。",
  },
  {
    type: "memory",
    title: "记忆",
    help: "记忆也是普通 Skill；可自动选择，也可固定到每次任务。",
  },
  {
    type: "workflow",
    title: "工作流",
    help: "工作流承载执行策略和结束条件，Runtime 负责实际调度。",
  },
]

export function SkillConfiguration(props: SkillConfigurationProps) {
  const knownTypes = new Set(knownGroups.map((group) => group.type))
  const customGroups = [...new Set(props.skills.map((skill) => skill.type))]
    .filter((type) => !knownTypes.has(type))
    .sort()
    .map((type) => ({
      type,
      title: type,
      help: `应用注册的 ${type} Skill；Runtime 通过同名 SkillLoader 加载。`,
    }))
  const groups = [...knownGroups, ...customGroups]

  return (
    <div className="configuration-sections">
      {groups.map((group) => {
        const skills = props.skills.filter((skill) => skill.type === group.type)
        return (
          <section key={group.type} className="configuration-section">
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
                    sameTypeSkills={skills}
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
  sameTypeSkills: SkillState[]
}

function SkillRow({
  skill,
  configuration,
  onChange,
  sameTypeSkills,
}: SkillRowProps) {
  const enabled =
    !configuration.disabled_skills.includes(skill.type) &&
    !configuration.disabled_skills.includes(skill.key) &&
    !configuration.disabled_skills.includes(skill.name)
  const selected =
    configuration.skills.includes(skill.key) ||
    configuration.skills.includes(skill.name)

  function setEnabled(nextEnabled: boolean): void {
    const withoutSkill = configuration.disabled_skills.filter(
      (item) => item !== skill.type && item !== skill.key && item !== skill.name
    )
    const withoutSelection = configuration.skills.filter(
      (item) => item !== skill.key && item !== skill.name
    )
    onChange({
      ...configuration,
      disabled_skills: nextEnabled
        ? withoutSkill
        : [...withoutSkill, skill.key],
      skills: nextEnabled ? configuration.skills : withoutSelection,
    })
  }

  function setSelected(nextSelected: boolean): void {
    const removable = new Set(
      (skill.type === "task" ? sameTypeSkills : [skill]).flatMap((item) => [
        item.key,
        item.name,
      ])
    )
    const withoutSkill = configuration.skills.filter(
      (item) => !removable.has(item)
    )
    onChange({
      ...configuration,
      skills: nextSelected ? [...withoutSkill, skill.key] : withoutSkill,
    })
  }

  return (
    <div className={cn("skill-row", !enabled && "skill-row-disabled")}>
      <div className="skill-row-main">
        <div className="skill-row-heading">
          <span className="skill-name">{skill.name}</span>
          <Badge variant="outline">v{skill.version}</Badge>
          <Badge variant="outline">{skill.type}</Badge>
          {skill.type === "task" && skill.default ? (
            <Badge variant="secondary">默认</Badge>
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
        <label className="skill-pin-control">
          <Checkbox
            checked={selected}
            disabled={!enabled}
            onCheckedChange={(checked) => setSelected(checked === true)}
          />
          {skill.type === "task" ? "固定任务" : "固定使用"}
          <HelpTooltip
            label={
              skill.type === "task"
                ? "固定后每次运行使用此任务 Skill；不固定时由模型按内容选择。"
                : "固定后每次任务都会加载；未固定时由渐进式披露按任务选择。"
            }
          />
        </label>
        <div className="skill-enable-control">
          <span>{enabled ? "已启用" : "已禁用"}</span>
          <Switch
            checked={enabled}
            onCheckedChange={setEnabled}
            aria-label={`${enabled ? "禁用" : "启用"} ${skill.name}`}
          />
        </div>
      </div>
    </div>
  )
}
