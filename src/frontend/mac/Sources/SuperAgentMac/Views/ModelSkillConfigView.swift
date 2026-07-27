import SwiftUI

private let modelProviderOptions = [
    "mock",
    "openai-compatible",
    "anthropic-compatible",
]
private let modelSupportOptions = ["text", "tools"]
private let modelPurposeOptions = [
    "answer",
    "planning",
    "research",
    "analysis",
    "synthesis",
    "summary",
    "coding",
    "skill_evolution",
    "skill_evaluation",
]

struct ModelSkillConfigEditorView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("模型 Skill")
                        .font(.headline)
                    Text("模型连接和调度特征保存在项目 Skill 目录；真实密钥只保存在 macOS 钥匙串。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    chatStore.refreshModelSkills()
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .help("从 Runtime 重新加载模型 Skill")
                Button {
                    chatStore.createModelSkill()
                } label: {
                    Image(systemName: "plus")
                }
                .help("新增模型 Skill")
            }

            if chatStore.modelSkillDrafts.isEmpty {
                EmptyChoiceTextView("未发现模型配置。新增后保存，或继续使用 Runtime 的零配置 mock 模型。")
            }

            ForEach(chatStore.modelSkillDrafts) { draft in
                ModelSkillEditorView(draft: draft)
            }
        }
    }
}

private struct ModelSkillEditorView: View {
    @EnvironmentObject private var chatStore: ChatStore
    let draft: ModelSkillDraft

    var body: some View {
        GroupBox {
            DisclosureGroup(isExpanded: expansionBinding) {
                VStack(alignment: .leading, spacing: 14) {
                    ModelSkillConnectionEditor(draft: draft)
                    Divider()
                    ModelSkillRoutingEditor(draft: draft)
                    Divider()
                    ModelSkillPermissionEditor(draft: draft)
                    ModelSkillActionBar(draft: draft)
                }
                .padding(.top, 12)
            } label: {
                ModelSkillHeader(draft: draft)
            }
        }
    }

    private var expansionBinding: Binding<Bool> {
        Binding(
            get: { chatStore.selectedModelSkillID == draft.id },
            set: { isExpanded in
                if isExpanded {
                    chatStore.selectModelSkill(draft.id)
                } else if chatStore.selectedModelSkillID == draft.id {
                    chatStore.selectedModelSkillID = nil
                }
            }
        )
    }
}

private struct ModelSkillHeader: View {
    let draft: ModelSkillDraft

    var body: some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 3) {
                Text("model:\(draft.name)")
                    .font(.headline.monospaced())
                Text(draft.summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if draft.isDefault {
                DefaultTagView(text: "默认")
            }
            if draft.ready {
                DefaultTagView(text: "就绪")
            }
            Text(draft.version)
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
        }
    }
}

private struct ModelSkillConnectionEditor: View {
    @EnvironmentObject private var chatStore: ChatStore
    let draft: ModelSkillDraft

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SettingTextField(
                title: "Skill 名称",
                help: "模型 Skill 的唯一名称，只能使用小写字母、数字、连字符或下划线。修改已保存名称后，Runtime 会原子重命名目录。",
                text: draftBinding(\.name)
            )
            SettingTextEditor(
                title: "模型说明",
                help: "说明模型适合处理什么任务。调度器和后续进化可以读取这段 Skill 描述。",
                text: draftBinding(\.description),
                height: 72
            )
            SettingStringPicker(
                title: "供应商协议",
                help: "mock 完全本地；OpenAI compatible 使用 chat/completions；Anthropic compatible 使用 messages。",
                selection: draftBinding(\.provider),
                options: modelProviderOptions
            )
            SettingTextField(
                title: "模型名称",
                help: "发送给供应商的真实模型标识，例如 gpt-4.1-mini 或本地模型名称。",
                text: draftBinding(\.model)
            )
            SettingTextField(
                title: "服务地址",
                help: "兼容服务的基础 URL。留空时使用所选供应商协议的默认地址。",
                text: draftBinding(\.baseUrl)
            )
            SettingTextField(
                title: "密钥环境变量名",
                help: "Skill TOML 只保存这个环境变量名，绝不会写入真实 API Key。",
                text: draftBinding(\.apiKeyEnv)
            )
            ModelCredentialEditor(draft: draft)
        }
    }

    private func draftBinding<Value>(
        _ keyPath: WritableKeyPath<ModelSkillDraft, Value>
    ) -> Binding<Value> {
        Binding(
            get: { draft[keyPath: keyPath] },
            set: { value in
                chatStore.updateModelSkillDraft(draft.id) { $0[keyPath: keyPath] = value }
            }
        )
    }
}

private struct ModelCredentialEditor: View {
    @EnvironmentObject private var chatStore: ChatStore
    let draft: ModelSkillDraft

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            FieldTitleView(
                title: "真实 API Key",
                help: "输入内容会在保存模型 Skill 时写入 macOS 钥匙串，不会进入 agent.toml、skill.toml 或桌面端 config.json。留空表示不更改。"
            )
            HStack {
                SecureField("留空表示不更改", text: credentialBinding)
                if draft.credentialStored {
                    DefaultTagView(text: "钥匙串")
                    Button("清除") {
                        chatStore.clearModelSkillCredential(draft.id)
                    }
                    .help("从 macOS 钥匙串删除这个环境变量对应的密钥")
                }
            }
        }
    }

    private var credentialBinding: Binding<String> {
        Binding(
            get: { draft.credential },
            set: { value in
                chatStore.updateModelSkillDraft(draft.id) { $0.credential = value }
            }
        )
    }
}

private struct ModelSkillRoutingEditor: View {
    @EnvironmentObject private var chatStore: ChatStore
    let draft: ModelSkillDraft

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            ModelStringOptionList(
                title: "支持能力",
                help: "任务需要工具时，只会优先选择勾选 tools 的模型；text 是所有对话的基础能力。",
                options: modelSupportOptions,
                values: draftBinding(\.supports)
            )
            ModelStringOptionList(
                title: "任务用途",
                help: "调度器会把普通回答、Skill 候选生成和 Skill 评价分配给匹配用途的模型。",
                options: modelPurposeOptions,
                values: draftBinding(\.purposes)
            )
            SettingTextField(
                title: "其它任务用途",
                help: "使用英文逗号分隔自定义 purpose。Agent.run(..., purpose: ...) 和自动任务识别都可以使用这些值。",
                text: additionalPurposesBinding
            )
            SettingTextField(
                title: "擅长内容",
                help: "使用英文逗号分隔。输入任务命中这些词时，调度器会提高该模型得分。",
                text: draftBinding(\.strengthsText)
            )
            SettingTextField(
                title: "用途触发词",
                help: "使用英文逗号分隔。它们保存在模型 Skill manifest 中，可供渐进式披露与后续进化读取。",
                text: draftBinding(\.triggersText)
            )
            ModelQualitySlider(value: draftBinding(\.qualityScore))
            ModelNumberSteppers(draft: draft)
        }
    }

    private func draftBinding<Value>(
        _ keyPath: WritableKeyPath<ModelSkillDraft, Value>
    ) -> Binding<Value> {
        Binding(
            get: { draft[keyPath: keyPath] },
            set: { value in
                chatStore.updateModelSkillDraft(draft.id) { $0[keyPath: keyPath] = value }
            }
        )
    }

    private var additionalPurposesBinding: Binding<String> {
        Binding(
            get: {
                draft.purposes
                    .filter { !modelPurposeOptions.contains($0) }
                    .joined(separator: ", ")
            },
            set: { value in
                let standard = draft.purposes.filter { modelPurposeOptions.contains($0) }
                chatStore.updateModelSkillDraft(draft.id) {
                    $0.purposes = standard + splitModelTags(value)
                }
            }
        )
    }
}

private struct ModelStringOptionList: View {
    let title: String
    let help: String
    let options: [String]
    @Binding var values: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            FieldTitleView(title: title, help: help)
            HStack(spacing: 18) {
                ForEach(options, id: \.self) { option in
                    Toggle(option, isOn: optionBinding(option))
                        .toggleStyle(.checkbox)
                }
            }
        }
    }

    private func optionBinding(_ option: String) -> Binding<Bool> {
        Binding(
            get: { values.contains(option) },
            set: { isSelected in
                if isSelected && !values.contains(option) {
                    values.append(option)
                } else if !isSelected {
                    values.removeAll { $0 == option }
                }
            }
        )
    }
}

private struct ModelQualitySlider: View {
    @Binding var value: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            FieldTitleView(
                title: "预设质量 \(value.formatted(.number.precision(.fractionLength(2))))",
                help: "冷启动调度使用的 0 到 1 质量先验。真实运行后，用户隔离的调用证据会逐步修正选择。"
            )
            Slider(value: $value, in: 0...1, step: 0.05)
        }
    }
}

private struct ModelNumberSteppers: View {
    @EnvironmentObject private var chatStore: ChatStore
    let draft: ModelSkillDraft

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Stepper(
                    "预期延迟：\(draft.expectedLatencyMs) ms",
                    value: intBinding(\.expectedLatencyMs),
                    in: 0...120_000,
                    step: 100
                )
                HelpHintView("冷启动时使用的延迟先验；实际调用延迟会作为用户与 Agent 隔离的路由证据持续更新。")
            }
            HStack {
                Stepper(
                    "输入成本：\(costText(draft.inputCostPerMillion)) / 百万 token",
                    value: doubleBinding(\.inputCostPerMillion),
                    in: 0...1_000,
                    step: 0.1
                )
                HelpHintView("每百万输入 token 的估算成本，仅用于调度比较和运行洞察。")
            }
            HStack {
                Stepper(
                    "输出成本：\(costText(draft.outputCostPerMillion)) / 百万 token",
                    value: doubleBinding(\.outputCostPerMillion),
                    in: 0...1_000,
                    step: 0.1
                )
                HelpHintView("每百万输出 token 的估算成本，仅用于调度比较和运行洞察。")
            }
        }
    }

    private func intBinding(
        _ keyPath: WritableKeyPath<ModelSkillDraft, Int>
    ) -> Binding<Int> {
        Binding(
            get: { draft[keyPath: keyPath] },
            set: { value in
                chatStore.updateModelSkillDraft(draft.id) { $0[keyPath: keyPath] = value }
            }
        )
    }

    private func doubleBinding(
        _ keyPath: WritableKeyPath<ModelSkillDraft, Double>
    ) -> Binding<Double> {
        Binding(
            get: { draft[keyPath: keyPath] },
            set: { value in
                chatStore.updateModelSkillDraft(draft.id) { $0[keyPath: keyPath] = value }
            }
        )
    }

    private func costText(_ value: Double) -> String {
        value.formatted(.number.precision(.fractionLength(2)))
    }
}

private struct ModelSkillPermissionEditor: View {
    @EnvironmentObject private var chatStore: ChatStore
    let draft: ModelSkillDraft

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            toggleLine(
                "设为默认模型",
                help: "默认模型获得冷启动优先级。保存后，Runtime 会自动取消其他模型 Skill 的默认状态。",
                keyPath: \.isDefault
            )
            toggleLine(
                "允许 Agent 优化调度说明",
                help: "允许 Agent 更新自己可管理的说明、用途、强项和调度参数。",
                keyPath: \.agentCanUpdate
            )
            toggleLine(
                "允许 Agent 修改连接",
                help: "允许 Agent 修改供应商、模型、地址或密钥变量名。默认关闭，真实密钥始终不能由 Agent 读取。",
                keyPath: \.agentCanUpdateConnection
            )
        }
    }

    private func toggleLine(
        _ title: String,
        help: String,
        keyPath: WritableKeyPath<ModelSkillDraft, Bool>
    ) -> some View {
        HStack {
            Toggle(title, isOn: boolBinding(keyPath))
                .toggleStyle(.checkbox)
            HelpHintView(help)
        }
    }

    private func boolBinding(
        _ keyPath: WritableKeyPath<ModelSkillDraft, Bool>
    ) -> Binding<Bool> {
        Binding(
            get: { draft[keyPath: keyPath] },
            set: { value in
                chatStore.updateModelSkillDraft(draft.id) { $0[keyPath: keyPath] = value }
            }
        )
    }
}

private struct ModelSkillActionBar: View {
    @EnvironmentObject private var chatStore: ChatStore
    let draft: ModelSkillDraft

    var body: some View {
        HStack {
            Text(sourceText)
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            if draft.isPersisted || draft.source == "draft" {
                Button(role: .destructive) {
                    chatStore.removeModelSkill(draft.id)
                } label: {
                    Image(systemName: "trash")
                }
                .help(draft.isPersisted ? "删除这个模型 Skill" : "放弃这个草稿")
            }
            Button("保存 Skill") {
                chatStore.saveModelSkill(draft.id)
            }
            .buttonStyle(.borderedProminent)
        }
    }

    private var sourceText: String {
        draft.isPersisted ? "已保存到项目 Skill 目录" : "来源：\(draft.source)"
    }
}
