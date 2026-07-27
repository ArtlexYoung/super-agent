import SwiftUI

struct RunInsightView: View {
    let insight: RuntimeRunInsight

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Divider()
            Text("运行洞察")
                .font(.title3.weight(.semibold))
            TaskScheduleInsightView(schedule: insight.schedule)
            ModelCallsInsightView(calls: insight.modelCalls)
            ModelRoutingEvidenceView(evidence: insight.routingEvidence)
            SkillFreshnessInsightView(skills: insight.skillFreshness)
            EvolutionInsightView(evolutions: insight.evolution)
        }
    }
}

private struct TaskScheduleInsightView: View {
    let schedule: RuntimeTaskSchedule

    var body: some View {
        InsightSectionView(title: "任务调度", icon: "point.3.connected.trianglepath.dotted") {
            HStack(spacing: 16) {
                InsightValueView(title: "用途", value: schedule.purpose ?? "answer")
                InsightValueView(title: "工作流", value: schedule.workflow ?? "-")
                InsightValueView(
                    title: "所需能力",
                    value: (schedule.requiredFeatures ?? []).joined(separator: ", ")
                )
            }
            if !(schedule.skills ?? []).isEmpty {
                InsightTextListView(title: "选择的 Skill", values: schedule.skills ?? [])
            }
            if !(schedule.subagents ?? []).isEmpty {
                InsightTextListView(title: "选择的子 Agent", values: schedule.subagents ?? [])
                InsightTextListView(title: "选择原因", values: schedule.subagentReasons ?? [])
            }
            ForEach(schedule.models ?? []) { model in
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(model.key)
                            .font(.body.monospaced().weight(.semibold))
                        Spacer()
                        Text("得分 \(model.score.formatted(.number.precision(.fractionLength(2))))")
                            .font(.caption.monospacedDigit())
                    }
                    Text(model.model)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    InsightTextListView(title: "调度原因", values: model.reasons)
                }
                .padding(10)
                .background(Color(nsColor: .textBackgroundColor).opacity(0.7))
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            }
        }
    }
}

private struct ModelCallsInsightView: View {
    let calls: [RuntimeModelCall]

    var body: some View {
        InsightSectionView(title: "模型调用", icon: "bolt.horizontal.circle") {
            if calls.isEmpty {
                InsightEmptyView(text: "这个运行没有模型调用证据。")
            }
            ForEach(calls) { call in
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Image(systemName: statusIcon(call.status))
                            .foregroundStyle(statusColor(call.status))
                        Text(call.profile ?? call.model ?? "未知模型")
                            .font(.body.monospaced().weight(.semibold))
                        Spacer()
                        Text(statusText(call.status))
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(statusColor(call.status))
                    }
                    HStack(spacing: 16) {
                        InsightValueView(title: "调用", value: "#\(call.callId)")
                        InsightValueView(title: "尝试", value: "#\(call.attempt)")
                        InsightValueView(title: "用途", value: call.purpose ?? "-")
                        InsightValueView(title: "延迟", value: milliseconds(call.latencyMs))
                        InsightValueView(title: "Token", value: tokenText(call))
                        InsightValueView(title: "估算成本", value: costText(call.estimatedCost))
                    }
                    if let reasons = call.reasons, !reasons.isEmpty {
                        InsightTextListView(title: "选择原因", values: reasons)
                    }
                    if let message = call.message, !message.isEmpty {
                        Text("\(call.errorType ?? "Error")：\(message)")
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                }
                .padding(10)
                .background(Color(nsColor: .textBackgroundColor).opacity(0.7))
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            }
        }
    }

    private func tokenText(_ call: RuntimeModelCall) -> String {
        "\(call.inputTokens ?? 0) / \(call.outputTokens ?? 0)"
    }
}

private struct ModelRoutingEvidenceView: View {
    let evidence: [RuntimeModelRoutingEvidence]

    var body: some View {
        InsightSectionView(title: "历史路由证据", icon: "chart.xyaxis.line") {
            if evidence.isEmpty {
                InsightEmptyView(text: "这个任务用途还没有可用的历史模型证据。")
            }
            ForEach(evidence) { item in
                VStack(alignment: .leading, spacing: 7) {
                    HStack {
                        Text(item.profileKey)
                            .font(.body.monospaced().weight(.semibold))
                        Spacer()
                        Text("\(item.callCount) 次 / \(item.successCount) 成功")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    HStack(spacing: 18) {
                        InsightValueView(title: "可靠性", value: percent(item.reliability))
                        InsightValueView(title: "平均质量", value: percent(item.averageQuality))
                        InsightValueView(
                            title: "平均延迟",
                            value: "\(Int(item.averageLatencyMs.rounded())) ms"
                        )
                        InsightValueView(
                            title: "平均 Token",
                            value: "\(Int(item.averageInputTokens.rounded())) / \(Int(item.averageOutputTokens.rounded()))"
                        )
                        InsightValueView(title: "平均成本", value: costText(item.averageCost))
                    }
                }
                .padding(.vertical, 4)
            }
        }
    }
}

private struct SkillFreshnessInsightView: View {
    let skills: [RuntimeSkillFreshness]

    var body: some View {
        InsightSectionView(title: "Skill 保鲜度", icon: "leaf.circle") {
            if skills.isEmpty {
                InsightEmptyView(text: "这个运行没有可归因的 Skill 保鲜度记录。")
            }
            ForEach(skills) { skill in
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(skill.skill)
                            .font(.body.monospaced().weight(.semibold))
                        Spacer()
                        Text(skill.freshness.formatted(.number.precision(.fractionLength(1))))
                            .font(.caption.monospacedDigit().weight(.semibold))
                            .foregroundStyle(freshnessColor(skill.freshness))
                    }
                    ProgressView(value: skill.freshness, total: 100)
                        .tint(freshnessColor(skill.freshness))
                    Text(
                        "分组 \(skill.functionGroup) · 调用 \(skill.callCount) · 成功 \(skill.successCount) · 错误 \(skill.errorCount) · Token \(skill.totalInputTokens + skill.totalOutputTokens) · 后续替代 \(skill.sameFunctionSuccessfulFollowups)"
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            }
        }
    }

    private func freshnessColor(_ value: Double) -> Color {
        if value >= 75 { return .green }
        if value >= 45 { return .orange }
        return .red
    }
}

private struct EvolutionInsightView: View {
    let evolutions: [RuntimeSkillEvolution]

    var body: some View {
        InsightSectionView(title: "自动进化", icon: "arrow.triangle.2.circlepath.circle") {
            if evolutions.isEmpty {
                InsightEmptyView(text: "本次运行没有触发 Skill 进化。")
            }
            ForEach(evolutions) { evolution in
                VStack(alignment: .leading, spacing: 7) {
                    HStack {
                        Text(evolution.skillKey)
                            .font(.body.monospaced().weight(.semibold))
                        if let revision = evolution.candidateRevision ?? evolution.sourceRevision {
                            Text("v\(revision.version)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        DefaultTagView(text: evolutionStatusText(evolution.status))
                    }
                    Text(evolution.goal)
                        .font(.callout)
                    InsightTextListView(title: "触发依据", values: evolution.reasons)
                    if let score = evolution.evaluationScore {
                        Text("候选评价：\(percent(score))")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if !evolution.candidateId.isEmpty {
                        Text("候选：\(evolution.candidateId)")
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                    }
                    if !evolution.detail.isEmpty {
                        Text(evolution.detail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(10)
                .background(Color(nsColor: .textBackgroundColor).opacity(0.7))
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            }
        }
    }
}

private struct InsightSectionView<Content: View>: View {
    let title: String
    let icon: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: icon)
                .font(.headline)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct InsightValueView: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.caption2)
                .foregroundStyle(.secondary)
            Text(value.isEmpty ? "-" : value)
                .font(.caption.monospacedDigit())
                .lineLimit(2)
        }
    }
}

private struct InsightTextListView: View {
    let title: String
    let values: [String]

    var body: some View {
        if !values.isEmpty {
            Text("\(title)：\(values.joined(separator: "；"))")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct InsightEmptyView: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(.secondary)
    }
}

private func statusIcon(_ status: String) -> String {
    status == "completed" ? "checkmark.circle.fill" :
        status == "failed" ? "xmark.circle.fill" : "clock"
}

private func statusColor(_ status: String) -> Color {
    status == "completed" ? .green : status == "failed" ? .red : .orange
}

private func statusText(_ status: String) -> String {
    status == "completed" ? "完成" : status == "failed" ? "失败" : "已选择"
}

private func evolutionStatusText(_ status: String) -> String {
    let values = [
        "candidate_recommended": "建议候选",
        "candidate_created": "已建候选",
        "evaluated": "评价通过",
        "promoted": "已晋升",
        "rejected": "已拒绝",
        "stable": "稳定",
        "rolled_back": "已回滚",
        "failed": "失败",
    ]
    return values[status] ?? status
}

private func percent(_ value: Double) -> String {
    value.formatted(.percent.precision(.fractionLength(1)))
}

private func milliseconds(_ value: Int?) -> String {
    value.map { "\($0) ms" } ?? "-"
}

private func costText(_ value: Double?) -> String {
    guard let value else { return "-" }
    return value.formatted(.currency(code: "USD").precision(.fractionLength(6)))
}
