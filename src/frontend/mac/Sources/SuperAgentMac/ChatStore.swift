import AppKit
import Combine
import Foundation
import UniformTypeIdentifiers

@MainActor
final class ChatStore: ObservableObject {
    @Published private(set) var conversations: [ChatConversation] {
        didSet { saveAppStateIfReady() }
    }
    @Published private(set) var selectedConversationID: UUID? {
        didSet { saveAppStateIfReady() }
    }
    @Published private(set) var selectedAgentRunNodeID: UUID?
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
    @Published private(set) var modelProfiles: [ModelProfile] {
        didSet { saveAppStateIfReady() }
    }
    @Published private(set) var selectedModelProfileID: UUID? {
        didSet { saveAppStateIfReady() }
    }
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

    init() {
        let state: StoredMacAppState?
        do {
            state = try MacAppStorage.loadState()
        } catch {
            state = nil
        }
        let initialConfig = state?.config ?? .defaultConfig
        let initialConversations = makeInitialConversations(from: state?.conversations)
        let selectedID = makeInitialSelectedConversationID(
            requestedID: state?.selectedConversationID,
            conversations: initialConversations
        )
        let fallbackProfile = ModelProfile(title: initialConfig.model.model, settings: initialConfig.model)
        let loadedProfiles = state?.modelProfiles ?? []
        let initialProfiles = loadedProfiles.isEmpty ? [fallbackProfile] : loadedProfiles
        let selectedProfileID = makeInitialSelectedModelProfileID(
            requestedID: state?.selectedModelProfileID,
            profiles: initialProfiles
        )
        let activeConfig = applySelectedModelProfile(
            selectedID: selectedProfileID,
            profiles: initialProfiles,
            config: initialConfig
        )

        conversations = initialConversations
        selectedConversationID = selectedID
        selectedAgentRunNodeID = nil
        config = activeConfig
        configTomlText = AgentTomlFile.makeTomlText(from: activeConfig)
        configFilePath = state?.configFilePath
        modelProfiles = initialProfiles
        selectedModelProfileID = selectedProfileID
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
        isReadyToSaveState = true
    }

    var selectedConversation: ChatConversation? {
        guard let selectedConversationID else {
            return conversations.first
        }
        return conversations.first { $0.id == selectedConversationID }
    }

    var selectedModelProfile: ModelProfile? {
        guard let selectedModelProfileID else {
            return modelProfiles.first
        }
        return modelProfiles.first { $0.id == selectedModelProfileID }
    }

    var selectedAgentRunNode: AgentRunNode? {
        guard let selectedAgentRunNodeID else {
            return nil
        }
        return findAgentRunNode(selectedAgentRunNodeID, in: selectedConversation?.agentRuns ?? [])
    }

    func createNewConversation() {
        let conversation = ChatConversation()
        conversations.insert(conversation, at: 0)
        selectedConversationID = conversation.id
        warningMessage = nil
    }

    func selectConversation(_ id: UUID) {
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
    }

    func deleteSelectedConversation() {
        guard let selectedConversationID else {
            return
        }
        deleteConversation(selectedConversationID)
    }

    func deleteConversation(_ id: UUID) {
        conversations.removeAll { $0.id == id }
        if conversations.isEmpty {
            createNewConversation()
            return
        }
        if selectedConversationID == id {
            selectedConversationID = conversations.first?.id
            selectedAgentRunNodeID = nil
        }
        warningMessage = nil
    }

    func clearSelectedConversationMessages() {
        updateSelectedConversation {
            $0.messages.removeAll()
            $0.agentRuns.removeAll()
        }
        selectedAgentRunNodeID = nil
    }

    func selectAgentRunNode(_ id: UUID?) {
        selectedAgentRunNodeID = id
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
        warningMessage = nil
        statusMessage = nil
        draftMessage = ""
        appendMessageToSelectedConversation(ChatMessage(role: .user, text: text))
        let messages = makeProviderMessagesForSelectedConversation()
        let configSnapshot = config
        let runtimeConfigText = makeRuntimeConfigText()
        isSendingMessage = true
        Task {
            do {
                let result = try await AgentRuntimeClient.run(
                    configText: runtimeConfigText,
                    prompt: text,
                    messages: messages
                )
                appendMessageToSelectedConversation(ChatMessage(role: .assistant, text: result.text))
                appendAgentRunToSelectedConversation(prompt: text, result: result)
                warningMessage = result.warningMessages?.joined(separator: "\n")
                statusMessage = "运行完成：\(result.workflow) / \(configSnapshot.modelSummary)"
            } catch {
                let detail = error.localizedDescription
                warningMessage = detail
                let failureText = "调用失败：\(detail)"
                appendMessageToSelectedConversation(ChatMessage(role: .assistant, text: failureText))
                appendFailedAgentRunToSelectedConversation(prompt: text, response: failureText)
            }
            isSendingMessage = false
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
            syncSelectedModelProfileFromConfig(title: config.model.model)
            refreshConfigChoices()
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
        syncSelectedModelProfileFromConfig(title: config.model.model)
        refreshConfigChoices()
        statusMessage = "已恢复默认 agent.toml 配置。"
    }

    func copyTomlTextToClipboard() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(configTomlText, forType: .string)
        statusMessage = "已复制 TOML。"
    }

    func createModelProfile() {
        let profile = ModelProfile(title: nextModelProfileTitle(), settings: config.model)
        modelProfiles.append(profile)
        selectModelProfile(profile.id)
    }

    func selectModelProfile(_ id: UUID) {
        guard let profile = modelProfiles.first(where: { $0.id == id }) else {
            return
        }
        selectedModelProfileID = id
        config.model = profile.settings
        statusMessage = "已切换模型配置：\(profile.title)。"
    }

    func deleteModelProfile(_ id: UUID) {
        modelProfiles.removeAll { $0.id == id }
        if modelProfiles.isEmpty {
            let profile = ModelProfile(title: "mock", settings: AgentTomlConfig.defaultConfig.model)
            modelProfiles = [profile]
        }
        if selectedModelProfileID == id || selectedModelProfileID == nil {
            selectModelProfile(modelProfiles[0].id)
        }
    }

    func updateModelProfile(_ id: UUID, change: (inout ModelProfile) -> Void) {
        guard let index = modelProfiles.firstIndex(where: { $0.id == id }) else {
            return
        }
        change(&modelProfiles[index])
        if selectedModelProfileID == id {
            config.model = modelProfiles[index].settings
        }
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
            syncSelectedModelProfileFromConfig(title: config.model.model)
            refreshConfigChoices()
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

    private func appendAgentRunToSelectedConversation(prompt: String, result: AgentRuntimeResult) {
        let run = makeAgentRunNode(agentName: config.agent.name, prompt: prompt, result: result)
        updateSelectedConversation { conversation in
            conversation.agentRuns.append(run)
        }
    }

    private func appendFailedAgentRunToSelectedConversation(prompt: String, response: String) {
        let run = AgentRunNode(
            agentName: config.agent.name,
            title: String(prompt.prefix(24)),
            prompt: prompt,
            response: response
        )
        updateSelectedConversation { conversation in
            conversation.agentRuns.append(run)
        }
    }

    private func makeAgentRunNode(
        agentName: String,
        prompt: String,
        result: AgentRuntimeResult
    ) -> AgentRunNode {
        AgentRunNode(
            runID: result.runId,
            agentName: agentName,
            title: String(prompt.prefix(24)),
            prompt: prompt,
            response: result.text,
            createdByAgent: false,
            children: (result.subagentResults ?? []).map(makeSubagentRunNode)
        )
    }

    private func makeSubagentRunNode(_ result: AgentRuntimeSubagentResult) -> AgentRunNode {
        AgentRunNode(
            runID: result.runId,
            agentName: result.name,
            title: result.description.isEmpty ? result.name : result.description,
            prompt: result.prompt,
            response: result.text,
            createdByAgent: result.createdByAgent,
            children: (result.subagentResults ?? []).map(makeSubagentRunNode)
        )
    }

    private func updateSelectedConversation(_ change: (inout ChatConversation) -> Void) {
        guard let selectedConversationID,
              let index = conversations.firstIndex(where: { $0.id == selectedConversationID }) else {
            return
        }
        change(&conversations[index])
        conversations[index].updatedAt = Date()
    }

    private func makeProviderMessagesForSelectedConversation() -> [ProviderChatMessage] {
        var messages: [ProviderChatMessage] = []
        for message in selectedConversation?.messages ?? [] {
            let role = message.role == .user ? "user" : "assistant"
            messages.append(ProviderChatMessage(role: role, content: message.text))
        }
        return messages
    }

    private func makeRuntimeConfigText() -> String {
        var runtimeConfig = config
        runtimeConfig.paths.skills = config.paths.skills.map { resolveConfigPath($0).path }
        runtimeConfig.paths.memory = resolveConfigPath(config.paths.memory).path
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
        return URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
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

    private func syncSelectedModelProfileFromConfig(title: String) {
        if let selectedModelProfileID,
           let index = modelProfiles.firstIndex(where: { $0.id == selectedModelProfileID }) {
            modelProfiles[index].settings = config.model
            if modelProfiles[index].title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                modelProfiles[index].title = title
            }
            return
        }
        let profile = ModelProfile(title: title, settings: config.model)
        modelProfiles.append(profile)
        selectedModelProfileID = profile.id
    }

    private func nextModelProfileTitle() -> String {
        var index = 1
        let titles = Set(modelProfiles.map(\.title))
        while true {
            let title = "模型配置 \(index)"
            if !titles.contains(title) {
                return title
            }
            index += 1
        }
    }

    private func saveAppStateIfReady() {
        guard isReadyToSaveState else {
            return
        }
        let state = StoredMacAppState(
            conversations: conversations,
            selectedConversationID: selectedConversationID,
            config: config,
            configFilePath: configFilePath,
            modelProfiles: modelProfiles,
            selectedModelProfileID: selectedModelProfileID
        )
        do {
            try MacAppStorage.saveState(state)
        } catch {
            statusMessage = "保存本地状态失败：\(error.localizedDescription)"
        }
    }
}

private func makeInitialConversations(from loadedConversations: [ChatConversation]?) -> [ChatConversation] {
    let conversations = loadedConversations ?? []
    return conversations.isEmpty ? [ChatConversation(title: "欢迎对话")] : conversations
}

private func makeInitialSelectedConversationID(
    requestedID: UUID?,
    conversations: [ChatConversation]
) -> UUID? {
    let fallbackID = conversations.first?.id
    guard let requestedID else {
        return fallbackID
    }
    return conversations.contains { $0.id == requestedID } ? requestedID : fallbackID
}

private func makeInitialSelectedModelProfileID(
    requestedID: UUID?,
    profiles: [ModelProfile]
) -> UUID? {
    let fallbackID = profiles.first?.id
    guard let requestedID else {
        return fallbackID
    }
    return profiles.contains { $0.id == requestedID } ? requestedID : fallbackID
}

private func applySelectedModelProfile(
    selectedID: UUID?,
    profiles: [ModelProfile],
    config: AgentTomlConfig
) -> AgentTomlConfig {
    var nextConfig = config
    if let selectedID,
       let profile = profiles.first(where: { $0.id == selectedID }) {
        nextConfig.model = profile.settings
    }
    return nextConfig
}
