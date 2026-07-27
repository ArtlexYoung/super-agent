import AppKit
import Combine
import Foundation
import UniformTypeIdentifiers

@MainActor
final class ChatStore: ObservableObject {
    @Published private(set) var conversations: [ChatConversation]
    @Published private(set) var selectedConversationID: String? {
        didSet { saveAppStateIfReady() }
    }
    @Published private(set) var selectedAgentRunNodeID: String? {
        didSet {
            if selectedAgentRunNodeID == nil {
                selectedRunInsight = nil
                isLoadingRunInsight = false
            }
        }
    }
    @Published var config: AgentTomlConfig {
        didSet {
            configTomlText = AgentTomlFile.makeTomlText(from: config)
            saveAppStateIfReady()
        }
    }
    @Published var configTomlText: String
    @Published private(set) var configFilePath: String? {
        didSet { saveAppStateIfReady() }
    }
    @Published var modelSkillDrafts: [ModelSkillDraft]
    @Published var selectedModelSkillID: UUID?
    @Published private(set) var selectedRunInsight: RuntimeRunInsight?
    @Published private(set) var isLoadingRunInsight: Bool
    @Published var draftMessage: String
    @Published var warningMessage: String?
    @Published var statusMessage: String?
    @Published private(set) var isSendingMessage: Bool
    @Published private(set) var availableSkillNames: [String]
    @Published private(set) var availableSkillChoices: [SkillManifestChoice]
    @Published private(set) var availableMemoryChoices: [SkillManifestChoice]
    @Published private(set) var availableWorkflowChoices: [SkillManifestChoice]
    @Published private(set) var availableMCPNames: [String]

    private var isReadyToSaveState = false
    private var pendingRenameTask: Task<Void, Never>?
    let runtimeUserID = "local"

    init() {
        let state: StoredMacAppState?
        do {
            state = try MacAppStorage.loadState()
        } catch {
            state = nil
        }
        let initialConfig = state?.config ?? .defaultConfig

        conversations = []
        selectedConversationID = state?.selectedConversationID
        selectedAgentRunNodeID = nil
        config = initialConfig
        configTomlText = AgentTomlFile.makeTomlText(from: initialConfig)
        configFilePath = state?.configFilePath
        modelSkillDrafts = []
        selectedModelSkillID = nil
        selectedRunInsight = nil
        isLoadingRunInsight = false
        draftMessage = ""
        warningMessage = nil
        statusMessage = nil
        isSendingMessage = false
        availableSkillNames = []
        availableSkillChoices = []
        availableMemoryChoices = []
        availableWorkflowChoices = []
        availableMCPNames = []
        refreshConfigChoices()
        refreshModelSkills()
        isReadyToSaveState = true
        reloadConversations()
    }

    var selectedConversation: ChatConversation? {
        guard let selectedConversationID else {
            return conversations.first
        }
        return conversations.first { $0.id == selectedConversationID }
    }

    var selectedModelSkillDraft: ModelSkillDraft? {
        guard let selectedModelSkillID else {
            return modelSkillDrafts.first
        }
        return modelSkillDrafts.first { $0.id == selectedModelSkillID }
    }

    var selectedModelSummary: String {
        let active = modelSkillDrafts.first { $0.isPersisted && $0.isDefault }
            ?? modelSkillDrafts.first { $0.isPersisted }
            ?? modelSkillDrafts.first
        return active?.summary ?? "自动发现"
    }

    var selectedAgentRunNode: AgentRunNode? {
        guard let selectedAgentRunNodeID else {
            return nil
        }
        return findAgentRunNode(selectedAgentRunNodeID, in: selectedConversation?.agentRuns ?? [])
    }

    func createNewConversation() {
        let configText = makeRuntimeConfigText()
        Task {
            do {
                let stored = try await AgentRuntimeClient.createConversation(
                    configText: configText,
                    userID: runtimeUserID
                )
                replaceConversation(with: stored)
                selectedConversationID = stored.conversationId
                selectedAgentRunNodeID = nil
                warningMessage = nil
            } catch {
                warningMessage = error.localizedDescription
            }
        }
    }

    func selectConversation(_ id: String) {
        selectedConversationID = id
        selectedAgentRunNodeID = nil
        warningMessage = nil
    }

    func renameSelectedConversation(to title: String) {
        let cleanTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanTitle.isEmpty else {
            return
        }
        updateSelectedConversation { $0.title = cleanTitle }
        guard let conversationID = selectedConversationID else {
            return
        }
        pendingRenameTask?.cancel()
        let configText = makeRuntimeConfigText()
        pendingRenameTask = Task {
            do {
                try await Task.sleep(nanoseconds: 350_000_000)
                let stored = try await AgentRuntimeClient.renameConversation(
                    configText: configText,
                    userID: runtimeUserID,
                    conversationID: conversationID,
                    title: cleanTitle
                )
                replaceConversation(with: stored)
                warningMessage = nil
            } catch is CancellationError {
                return
            } catch {
                warningMessage = error.localizedDescription
            }
        }
    }

    func deleteSelectedConversation() {
        guard let selectedConversationID else {
            return
        }
        deleteConversation(selectedConversationID)
    }

    func deleteConversation(_ id: String) {
        pendingRenameTask?.cancel()
        let configText = makeRuntimeConfigText()
        Task {
            do {
                try await AgentRuntimeClient.deleteConversation(
                    configText: configText,
                    userID: runtimeUserID,
                    conversationID: id
                )
                conversations.removeAll { $0.id == id }
                if conversations.isEmpty {
                    let stored = try await AgentRuntimeClient.createConversation(
                        configText: configText,
                        userID: runtimeUserID
                    )
                    conversations = [makeChatConversation(from: stored)]
                }
                if selectedConversationID == id {
                    selectedConversationID = conversations.first?.id
                    selectedAgentRunNodeID = nil
                }
                warningMessage = nil
            } catch {
                warningMessage = error.localizedDescription
            }
        }
    }

    func clearSelectedConversationMessages() {
        guard let conversationID = selectedConversationID else {
            return
        }
        let configText = makeRuntimeConfigText()
        Task {
            do {
                let stored = try await AgentRuntimeClient.clearConversation(
                    configText: configText,
                    userID: runtimeUserID,
                    conversationID: conversationID
                )
                replaceConversation(with: stored)
                selectedAgentRunNodeID = nil
                warningMessage = nil
            } catch {
                warningMessage = error.localizedDescription
            }
        }
    }

    func selectAgentRunNode(_ id: String?) {
        selectedRunInsight = nil
        isLoadingRunInsight = false
        selectedAgentRunNodeID = id
        guard let node = selectedAgentRunNode, let runID = node.runID else {
            return
        }
        loadRunInsight(runID: runID, nodeID: node.id)
    }

    private func loadRunInsight(runID: String, nodeID: String) {
        let configText = makeRuntimeConfigText()
        isLoadingRunInsight = true
        Task {
            defer {
                if selectedAgentRunNodeID == nodeID {
                    isLoadingRunInsight = false
                }
            }
            do {
                let insight = try await AgentRuntimeClient.readRunInsight(
                    configText: configText,
                    userID: runtimeUserID,
                    runID: runID
                )
                guard selectedAgentRunNodeID == nodeID else { return }
                selectedRunInsight = insight
                warningMessage = nil
            } catch {
                guard selectedAgentRunNodeID == nodeID else { return }
                warningMessage = error.localizedDescription
            }
        }
    }

    func reloadConversations() {
        let configText = makeRuntimeConfigText()
        Task {
            do {
                var stored = try await AgentRuntimeClient.listConversations(
                    configText: configText,
                    userID: runtimeUserID
                )
                if stored.isEmpty {
                    stored = [
                        try await AgentRuntimeClient.createConversation(
                            configText: configText,
                            userID: runtimeUserID
                        )
                    ]
                }
                applyRuntimeConversations(stored)
                warningMessage = nil
            } catch {
                warningMessage = error.localizedDescription
            }
        }
    }

    func sendDraftMessage() {
        let text = draftMessage.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            warningMessage = "请输入消息后再发送。"
            return
        }
        guard !isSendingMessage else {
            return
        }
        guard let conversationID = selectedConversationID else {
            warningMessage = "对话仍在加载，请稍后再试。"
            return
        }
        warningMessage = nil
        statusMessage = nil
        draftMessage = ""
        let pendingMessageID = "pending-\(UUID().uuidString)"
        appendMessageToSelectedConversation(
            ChatMessage(id: pendingMessageID, role: .user, text: text)
        )
        let modelSummary = selectedModelSummary
        let runtimeConfigText = makeRuntimeConfigText()
        isSendingMessage = true
        Task {
            defer { isSendingMessage = false }
            do {
                let result = try await AgentRuntimeClient.run(
                    configText: runtimeConfigText,
                    prompt: text,
                    userID: runtimeUserID,
                    conversationID: conversationID
                )
                let stored = try await AgentRuntimeClient.readConversation(
                    configText: runtimeConfigText,
                    userID: runtimeUserID,
                    conversationID: conversationID
                )
                replaceConversation(with: stored)
                warningMessage = result.warningMessages?.joined(separator: "\n")
                statusMessage = "运行完成：\(result.workflow) / \(modelSummary)"
            } catch {
                let detail = error.localizedDescription
                warningMessage = detail
                if let stored = try? await AgentRuntimeClient.readConversation(
                    configText: runtimeConfigText,
                    userID: runtimeUserID,
                    conversationID: conversationID
                ) {
                    replaceConversation(with: stored)
                } else {
                    updateSelectedConversation {
                        $0.messages.removeAll { $0.id == pendingMessageID }
                    }
                }
            }
        }
    }

    func openTomlConfigFile() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [UTType(filenameExtension: "toml") ?? .plainText]
        guard panel.runModal() == .OK, let url = panel.url else {
            return
        }
        loadTomlConfig(from: url)
    }

    func saveTomlConfigFile() {
        guard let configFilePath else {
            saveTomlConfigFileAs()
            return
        }
        saveTomlConfig(to: URL(fileURLWithPath: configFilePath))
    }

    func saveTomlConfigFileAs() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "agent.toml"
        panel.allowedContentTypes = [UTType(filenameExtension: "toml") ?? .plainText]
        guard panel.runModal() == .OK, let url = panel.url else {
            return
        }
        saveTomlConfig(to: url)
    }

    func applyTomlTextToConfig() {
        do {
            config = try AgentTomlFile.parse(configTomlText)
            refreshConfigChoices()
            refreshModelSkills()
            reloadConversations()
            statusMessage = "已把 TOML 文本应用到可视化表单。"
            warningMessage = nil
        } catch {
            warningMessage = error.localizedDescription
        }
    }

    func refreshTomlTextFromConfig() {
        configTomlText = AgentTomlFile.makeTomlText(from: config)
        statusMessage = "已从表单重新生成 TOML。"
    }

    func resetConfigToDefault() {
        config = .defaultConfig
        configFilePath = nil
        refreshConfigChoices()
        refreshModelSkills()
        reloadConversations()
        statusMessage = "已恢复默认 agent.toml 配置。"
    }

    func copyTomlTextToClipboard() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(configTomlText, forType: .string)
        statusMessage = "已复制 TOML。"
    }

    func refreshConfigChoices() {
        let configText = makeRuntimeConfigText()
        Task {
            do {
                let choices = try await SkillIndexClient.readSkillChoices(configText: configText)
                applySkillChoices(choices)
                statusMessage = "已从中心披露核心刷新可选配置项。"
                warningMessage = nil
            } catch {
                warningMessage = error.localizedDescription
            }
        }
    }

    private func applySkillChoices(_ scannedChoices: [SkillManifestChoice]) {
        let allSkillChoices = mergeSkillChoices(
            configuredNames: config.agent.skills,
            scannedChoices: scannedChoices
        )
        // Group the shared Skill tree by Capability for focused configuration lists.
        availableSkillChoices = allSkillChoices.filter { $0.capability == "prompt" }
        availableMemoryChoices = allSkillChoices.filter { $0.capability == "memory" }
        availableWorkflowChoices = allSkillChoices.filter { $0.capability == "workflow" }
        availableSkillNames = allSkillChoices.map(\.name)
        let mcpNames = allSkillChoices.filter { $0.capability == "mcp" }.map(\.name)
        availableMCPNames = sortedUnique(extractDisabledNames(prefix: "mcp:") + mcpNames)
    }

    private func loadTomlConfig(from url: URL) {
        do {
            config = try AgentTomlFile.read(from: url)
            configFilePath = url.path
            refreshConfigChoices()
            refreshModelSkills()
            reloadConversations()
            statusMessage = "已加载 \(url.lastPathComponent)。"
            warningMessage = nil
        } catch {
            warningMessage = error.localizedDescription
        }
    }

    private func saveTomlConfig(to url: URL) {
        do {
            try AgentTomlFile.write(config, to: url)
            configFilePath = url.path
            refreshConfigChoices()
            refreshModelSkills()
            statusMessage = "已保存 \(url.lastPathComponent)。"
            warningMessage = nil
        } catch {
            warningMessage = error.localizedDescription
        }
    }

    private func appendMessageToSelectedConversation(_ message: ChatMessage) {
        updateSelectedConversation { conversation in
            if conversation.messages.isEmpty && message.role == .user {
                conversation.title = String(message.text.prefix(18))
            }
            conversation.messages.append(message)
        }
    }

    private func applyRuntimeConversations(_ stored: [RuntimeConversation]) {
        conversations = stored.map(makeChatConversation)
        if let selectedConversationID,
           conversations.contains(where: { $0.id == selectedConversationID }) {
            return
        }
        selectedConversationID = conversations.first?.id
        selectedAgentRunNodeID = nil
    }

    private func replaceConversation(with stored: RuntimeConversation) {
        let conversation = makeChatConversation(from: stored)
        conversations.removeAll { $0.id == conversation.id }
        conversations.insert(conversation, at: 0)
        selectedAgentRunNodeID = nil
    }

    private func updateSelectedConversation(_ change: (inout ChatConversation) -> Void) {
        guard let selectedConversationID,
              let index = conversations.firstIndex(where: { $0.id == selectedConversationID }) else {
            return
        }
        change(&conversations[index])
        conversations[index].updatedAt = Date()
    }

    func makeRuntimeConfigText() -> String {
        var runtimeConfig = config
        runtimeConfig.paths.skills = config.paths.skills.map { resolveConfigPath($0).path }
        runtimeConfig.storage.path = resolveConfigPath(config.storage.path).path
        return AgentTomlFile.makeTomlText(from: runtimeConfig)
    }

    private func resolveConfigPath(_ path: String) -> URL {
        let expandedPath = NSString(string: path).expandingTildeInPath
        if expandedPath.hasPrefix("/") {
            return URL(fileURLWithPath: expandedPath)
        }
        return configBaseDirectory().appendingPathComponent(expandedPath)
    }

    private func configBaseDirectory() -> URL {
        if let configFilePath {
            return URL(fileURLWithPath: configFilePath).deletingLastPathComponent()
        }
        return (try? MacAppStorage.defaultProjectDirectoryURL())
            ?? URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
    }

    private func extractDisabledNames(prefix: String) -> [String] {
        config.agent.disableNames.compactMap { name in
            name.hasPrefix(prefix) ? String(name.dropFirst(prefix.count)) : nil
        }
    }

    private func sortedUnique(_ values: [String]) -> [String] {
        Array(Set(values.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty })).sorted()
    }

    private func mergeSkillChoices(
        configuredNames: [String],
        scannedChoices: [SkillManifestChoice]
    ) -> [SkillManifestChoice] {
        var choicesByName: [String: SkillManifestChoice] = [:]
        for choice in scannedChoices {
            choicesByName["\(choice.capability):\(choice.name)"] = choice
        }
        for name in configuredNames {
            let key = "prompt:\(name)"
            if choicesByName[key] == nil {
                choicesByName[key] = SkillManifestChoice(
                    key: key,
                    name: name,
                    capability: "prompt",
                    agentCreated: false,
                    agentCanUpdate: false,
                    functionGroup: name
                )
            }
        }
        return choicesByName.values.sorted { $0.name < $1.name }
    }

    private func saveAppStateIfReady() {
        guard isReadyToSaveState else {
            return
        }
        let state = StoredMacAppState(
            selectedConversationID: selectedConversationID,
            config: config,
            configFilePath: configFilePath
        )
        do {
            try MacAppStorage.saveState(state)
        } catch {
            statusMessage = "保存本地状态失败：\(error.localizedDescription)"
        }
    }
}
