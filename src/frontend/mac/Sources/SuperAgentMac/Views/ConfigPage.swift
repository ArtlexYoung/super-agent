import SwiftUI

private let providerOptions = ["auto", "mock", "openai-compatible", "anthropic-compatible"]
private let modelOptions = ["mock", "gpt-4.1-mini", "gpt-4.1", "claude-sonnet-4", "claude-opus-4"]
private let baseURLOptions = ["", "https://api.openai.com/v1", "https://api.anthropic.com"]
private let apiKeyEnvOptions = ["", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"]

private enum ConfigPageTab: String, CaseIterable, Identifiable {
    case agent = "助手"
    case model = "模型"
    case paths = "路径"
    case toml = "原文"

    var id: String { rawValue }
}

struct ConfigPageView: View {
    @State private var selectedTab: ConfigPageTab = .agent

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text("配置")
                    .font(.largeTitle.weight(.semibold))
                Text("勾选即打开，默认行为用标签和按钮切换。高级修改可在 TOML 原文里完成。")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            ConfigFileToolbarView()
            ConfigTabPickerView(selectedTab: $selectedTab)
            ConfigPageContentView(selectedTab: selectedTab)
        }
        .padding(28)
    }
}

private struct ConfigTabPickerView: View {
    @Binding var selectedTab: ConfigPageTab

    var body: some View {
        Picker("配置分类", selection: $selectedTab) {
            ForEach(ConfigPageTab.allCases) { tab in
                Text(tab.rawValue).tag(tab)
            }
        }
        .pickerStyle(.segmented)
    }
}

private struct ConfigPageContentView: View {
    let selectedTab: ConfigPageTab

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                switch selectedTab {
                case .agent:
                    AgentConfigEditorView()
                case .model:
                    ModelConfigEditorView()
                case .paths:
                    PathsConfigEditorView()
                case .toml:
                    TomlTextEditorView()
                }
            }
            .padding(.bottom, 24)
        }
    }
}

private struct ConfigFileToolbarView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Button("打开配置") { chatStore.openTomlConfigFile() }
                    Button("保存") { chatStore.saveTomlConfigFile() }
                    Button("另存为") { chatStore.saveTomlConfigFileAs() }
                    Button("恢复默认") { chatStore.resetConfigToDefault() }
                    Button("重新扫描") { chatStore.refreshConfigChoices() }
                    HelpHintView("打开或保存当前 agent.toml。重新扫描会递归读取技能目录，刷新普通技能和 MCP 列表。")
                }
                .buttonStyle(.bordered)

                Text(chatStore.configFilePath ?? "未绑定文件。保存时会选择 agent.toml 路径。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .textSelection(.enabled)
            }
        }
    }
}

private struct AgentConfigEditorView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ConfigSectionBox(title: "基础") {
                SettingTextField(
                    title: "助手名称",
                    help: "对应 [agent].name。用于标识当前 agent，也会出现在多 agent 链路提醒里。",
                    text: $chatStore.config.agent.name
                )
                SettingTextEditor(
                    title: "系统提示词",
                    help: "对应 [agent].system。这里写入 agent 的基础角色、边界和回答风格。",
                    text: $chatStore.config.agent.system,
                    height: 120
                )
            }

            WorkflowDefaultView()
            MemoryDefaultView()
            SkillFeatureView()
            SkillManifestListView()
            MCPManifestListView()
            MaxDepthEditorView()
        }
    }
}

private struct WorkflowDefaultView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        ConfigSectionBox(title: "工作流") {
            HStack {
                FieldTitleView(
                    title: "默认工作流",
                    help: "对应 [agent].workflow。默认标签表示当前 agent 运行时采用的 workflow。"
                )
                DefaultTagView(text: chatStore.config.agent.workflow)
                Button("恢复 direct") {
                    chatStore.config.agent.workflow = "direct"
                }
            }

            ChoiceButtonListView(
                choices: workflowChoices,
                selectedID: chatStore.config.agent.workflow,
                buttonTitle: "设为默认"
            ) { choice in
                chatStore.config.agent.workflow = choice.id
            }
        }
    }

    private var workflowChoices: [ToggleChoice] {
        if chatStore.availableWorkflowChoices.isEmpty {
            return [ToggleChoice(id: chatStore.config.agent.workflow, title: chatStore.config.agent.workflow, help: "当前配置的 workflow skill。")]
        }
        return chatStore.availableWorkflowChoices.map {
            ToggleChoice(id: $0.name, title: $0.name, help: "来自中心披露索引中的 workflow Skill。")
        }
    }
}

private struct MemoryDefaultView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        ConfigSectionBox(title: "记忆") {
            HStack {
                FieldTitleView(
                    title: "默认记忆行为",
                    help: "对应 [agent].memory。运行时会选择 capability = \"memory\" 的同名 Skill，数据统一交给 [storage] 保存。"
                )
                DefaultTagView(text: chatStore.config.agent.memory)
                Button("恢复 default") {
                    chatStore.config.agent.memory = "default"
                }
            }
            ChoiceButtonListView(
                choices: memoryChoices,
                selectedID: chatStore.config.agent.memory,
                buttonTitle: "设为默认"
            ) { choice in
                chatStore.config.agent.memory = choice.id
            }
            Toggle(
                "允许当前记忆",
                isOn: Binding(
                    get: { memoryIsOpen(chatStore.config.agent.memory, config: chatStore.config) },
                    set: { setMemoryOpen(chatStore.config.agent.memory, isOpen: $0, config: $chatStore.config) }
                )
            )
            .toggleStyle(.checkbox)
            .help("关闭会写入 disable_names = [\"memory:\(chatStore.config.agent.memory)\"]。")
        }
    }

    private var memoryChoices: [ToggleChoice] {
        if chatStore.availableMemoryChoices.isEmpty {
            return [ToggleChoice(id: chatStore.config.agent.memory, title: chatStore.config.agent.memory, help: "当前配置的 memory skill。")]
        }
        return chatStore.availableMemoryChoices.map {
            ToggleChoice(id: $0.name, title: $0.name, help: "来自中心披露索引中的 memory Skill。")
        }
    }
}

private struct SkillFeatureView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        ConfigSectionBox(title: "skill") {
            Toggle(
                "打开普通 skill 能力",
                isOn: Binding(
                    get: { featureIsEnabled("skill", config: chatStore.config) },
                    set: { setFeatureEnabled("skill", isEnabled: $0, config: $chatStore.config) }
                )
            )
            .toggleStyle(.checkbox)
            .help("对应 [agent].use_features 里的 skill。打开后才会加载普通 skill 目录。")
        }
    }
}

private struct SkillManifestListView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        ConfigSectionBox(title: "技能") {
            if chatStore.availableSkillChoices.isEmpty {
                EmptyChoiceTextView("中心披露索引中没有可用的 prompt Skill。")
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 260), alignment: .leading)], alignment: .leading) {
                    ForEach(chatStore.availableSkillChoices) { choice in
                        SkillChoiceToggleView(choice: choice, isOpen: skillIsOpenBinding(choice))
                    }
                }
            }
        }
    }

    private func skillIsOpenBinding(_ choice: SkillManifestChoice) -> Binding<Bool> {
        Binding(
            get: {
                skillIsOpen(
                    choice.name,
                    capability: choice.capability,
                    config: chatStore.config
                )
            },
            set: {
                setSkillOpen(
                    choice.name,
                    capability: choice.capability,
                    isOpen: $0,
                    config: $chatStore.config
                )
            }
        )
    }
}

private struct SkillChoiceToggleView: View {
    let choice: SkillManifestChoice
    let isOpen: Binding<Bool>

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Toggle(choice.name, isOn: isOpen)
                    .toggleStyle(.checkbox)
                if choice.agentCreated {
                    DefaultTagView(text: "自建")
                }
                if choice.agentCanUpdate {
                    DefaultTagView(text: "可更新")
                }
                if choice.freshness < 45 {
                    DefaultTagView(text: "建议优化")
                }
            }

            SkillFreshnessBarView(choice: choice)

            HStack(spacing: 8) {
                Text("分组：\(choice.functionGroup)")
                if choice.hasRuntimeStats {
                    Text("调用：\(choice.callCount)")
                    Text("成功：\(choice.successCount)")
                    Text("替代：\(choice.replacementCount)")
                } else {
                    Text("配置值")
                }
            }
            .font(.caption2)
            .foregroundStyle(.secondary)
            .lineLimit(1)
        }
        .padding(10)
        .background(Color(nsColor: .textBackgroundColor).opacity(0.72))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .help(helpText)
    }

    private var helpText: String {
        "保鲜度、调用次数和替代统计均来自中心披露索引，桌面端不再单独读取运行数据。"
    }
}

private struct SkillFreshnessBarView: View {
    let choice: SkillManifestChoice

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("保鲜度")
                    .font(.caption.weight(.semibold))
                Spacer()
                Text("\(Int(choice.freshness.rounded()))")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(freshnessColor)
            }
            ProgressView(value: min(max(choice.freshness, 0), 100), total: 100)
                .tint(freshnessColor)
        }
    }

    private var freshnessColor: Color {
        if choice.freshness >= 75 {
            return .green
        }
        if choice.freshness >= 45 {
            return .orange
        }
        return .red
    }
}

private struct MCPManifestListView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        ConfigSectionBox(title: "MCP") {
            Toggle(
                "允许 MCP 类型",
                isOn: Binding(
                    get: { skillCapabilityIsEnabled("mcp", config: chatStore.config) },
                    set: {
                        setSkillCapabilityEnabled(
                            "mcp",
                            isEnabled: $0,
                            config: $chatStore.config
                        )
                    }
                )
            )
            .toggleStyle(.checkbox)
            .help("MCP 不再是独立 use_features。关闭会写入 disable_names = [\"mcp\"]，运行时仍从 [paths].skills 递归扫描 capability = \"mcp\" 的 Skill。")

            if chatStore.availableMCPNames.isEmpty {
                EmptyChoiceTextView("中心披露索引中没有可用的 MCP Skill。")
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 180), alignment: .leading)], alignment: .leading) {
                    ForEach(chatStore.availableMCPNames, id: \.self) { name in
                        Toggle(name, isOn: mcpIsOpenBinding(name))
                            .toggleStyle(.checkbox)
                            .help("勾选后会加入 [agent].skills。取消勾选会写入 disable_names = [\"mcp:\(name)\"]。")
                    }
                }
            }
        }
    }

    private func mcpIsOpenBinding(_ name: String) -> Binding<Bool> {
        Binding(
            get: { mcpIsOpen(name, config: chatStore.config) },
            set: { setMCPOpen(name, isOpen: $0, config: $chatStore.config) }
        )
    }
}

private struct MaxDepthEditorView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        ConfigSectionBox(title: "嵌套提醒") {
            FieldTitleView(
                title: "深度提醒",
                help: "对应 [agent].max_agent_chain_depth。超过该层数只提醒，不阻止执行；不启用时表示无限深度。"
            )
            Toggle("打开深度提醒", isOn: isEnabled)
                .toggleStyle(.checkbox)
            if chatStore.config.agent.maxAgentChainDepth != nil {
                Stepper("提醒阈值：\(depth.wrappedValue) 层", value: depth, in: 1...99)
            }
        }
    }

    private var isEnabled: Binding<Bool> {
        Binding(
            get: { chatStore.config.agent.maxAgentChainDepth != nil },
            set: { chatStore.config.agent.maxAgentChainDepth = $0 ? 5 : nil }
        )
    }

    private var depth: Binding<Int> {
        Binding(
            get: { chatStore.config.agent.maxAgentChainDepth ?? 5 },
            set: { chatStore.config.agent.maxAgentChainDepth = max(1, $0) }
        )
    }
}

private struct ModelConfigEditorView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("模型列表")
                        .font(.headline)
                    Text("配置会自动保存到桌面端 config.json，下次打开会恢复。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("新增模型") {
                    chatStore.createModelProfile()
                }
            }

            ForEach(chatStore.modelProfiles) { profile in
                ModelProfileRowView(profile: profile)
            }
        }
    }
}

private struct ModelProfileRowView: View {
    @EnvironmentObject private var chatStore: ChatStore
    let profile: ModelProfile

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    TextField("配置名称", text: profileTitleBinding)
                        .font(.headline)
                    if chatStore.selectedModelProfileID == profile.id {
                        DefaultTagView(text: "当前")
                    }
                    Button(chatStore.selectedModelProfileID == profile.id ? "使用中" : "使用") {
                        chatStore.selectModelProfile(profile.id)
                    }
                    .disabled(chatStore.selectedModelProfileID == profile.id)
                    Button {
                        chatStore.deleteModelProfile(profile.id)
                    } label: {
                        Image(systemName: "trash")
                    }
                    .buttonStyle(.plain)
                    .help("删除这个模型配置")
                }

                SettingStringPicker(
                    title: "服务类型",
                    help: "对应 [model].provider。auto 按环境自动解析；mock 是本地回复；openai-compatible 请求 /chat/completions；anthropic-compatible 请求 /v1/messages。",
                    selection: providerBinding,
                    options: providerOptions
                )
                SettingTextField(
                    title: "模型名称",
                    help: "对应 [model].model。可输入任意模型名，保存后下次打开会恢复。",
                    text: modelBinding
                )
                SettingTextField(
                    title: "服务地址",
                    help: "对应 [model].base_url。可输入任意兼容服务地址；留空时使用 provider 的默认地址。",
                    text: baseURLBinding
                )
                SettingTextField(
                    title: "密钥变量",
                    help: "对应 [model].api_key_env。这里输入环境变量名，不直接保存真实 API Key。",
                    text: apiKeyEnvBinding
                )
            }
            .padding(.top, 4)
        }
    }

    private var profileTitleBinding: Binding<String> {
        Binding(
            get: { profile.title },
            set: { value in
                chatStore.updateModelProfile(profile.id) { $0.title = value }
            }
        )
    }

    private var providerBinding: Binding<String> {
        Binding(
            get: { profile.settings.provider },
            set: { value in
                chatStore.updateModelProfile(profile.id) { $0.settings.provider = value }
            }
        )
    }

    private var modelBinding: Binding<String> {
        Binding(
            get: { profile.settings.model },
            set: { value in
                chatStore.updateModelProfile(profile.id) { $0.settings.model = value }
            }
        )
    }

    private var baseURLBinding: Binding<String> {
        Binding(
            get: { profile.settings.baseURL },
            set: { value in
                chatStore.updateModelProfile(profile.id) { $0.settings.baseURL = value }
            }
        )
    }

    private var apiKeyEnvBinding: Binding<String> {
        Binding(
            get: { profile.settings.apiKeyEnv },
            set: { value in
                chatStore.updateModelProfile(profile.id) { $0.settings.apiKeyEnv = value }
            }
        )
    }
}

private struct PathsConfigEditorView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        ConfigSectionBox(title: "路径与存储") {
            DirectoryListView(
                title: "技能目录",
                help: "对应 [paths].skills。runtime 会在这些目录下递归查找 prompt、MCP、memory、workflow 等 skill。",
                items: $chatStore.config.paths.skills,
                defaultName: "skills"
            )
            Picker("存储后端", selection: $chatStore.config.storage.backend) {
                Text("JSONL（默认）").tag("jsonl")
                Text("SQLite").tag("sqlite")
                Text("MySQL").tag("mysql")
                Text("PostgreSQL").tag("postgresql")
            }
            .help("对应 [storage].backend。JSONL 默认且零依赖；SQLite 适合本地并发；MySQL 与 PostgreSQL 适合共享部署并按需加载驱动。")
            DirectoryPathView(
                title: "本地存储目录",
                help: "对应 [storage].path。JSONL 事件、SQLite 数据库、披露缓存和进化工作区都在这里隔离；远程后端只把标准事件放入数据库。",
                path: $chatStore.config.storage.path,
                defaultName: ".super-agent"
            )
            if ["mysql", "postgresql"].contains(chatStore.config.storage.backend) {
                TextField("连接地址环境变量", text: $chatStore.config.storage.urlEnv)
                    .help("对应 [storage].url_env，只保存环境变量名，不保存连接地址或密码。留空时 MySQL 使用 SUPER_AGENT_MYSQL_URL，PostgreSQL 使用 SUPER_AGENT_POSTGRESQL_URL。")
            }
        }
    }
}

private struct TomlTextEditorView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        ConfigSectionBox(title: "TOML 原文") {
            HStack {
                Button("应用到表单") { chatStore.applyTomlTextToConfig() }
                Button("从表单生成") { chatStore.refreshTomlTextFromConfig() }
                Button("复制") { chatStore.copyTomlTextToClipboard() }
                HelpHintView("这里可以直接编辑 agent.toml 原文。应用到表单会解析文本；从表单生成会用当前可视化配置覆盖文本。")
            }
            .buttonStyle(.bordered)

            TextEditor(text: $chatStore.configTomlText)
                .font(.system(.body, design: .monospaced))
                .frame(minHeight: 420)
                .padding(8)
                .background(Color(nsColor: .textBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
    }
}
