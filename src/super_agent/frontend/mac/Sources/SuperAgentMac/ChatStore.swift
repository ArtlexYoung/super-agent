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

    func createNewConversation() {
        let conversation = ChatConversation()
        conversations.insert(conversation, at: 0)
        selectedConversationID = conversation.id
        warningMessage = nil
    }

    func selectConversation(_ id: UUID) {
        selectedConversationID = id
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
        }
        warningMessage = nil
    }

    func clearSelectedConversationMessages() {
        updateSelectedConversation { $0.messages.removeAll() }
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
        isSendingMessage = true
        Task {
            do {
                let reply = try await ModelChatClient.sendReply(config: configSnapshot, messages: messages)
                appendMessageToSelectedConversation(ChatMessage(role: .assistant, text: reply))
                statusMessage = "已使用 \(configSnapshot.modelSummary) 回复。"
            } catch {
                let detail = error.localizedDescription
                warningMessage = detail
                appendMessageToSelectedConversation(ChatMessage(role: .assistant, text: "调用失败：\(detail)"))
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
        let skillNames = readSkillNamesFromConfiguredPaths()
        let mcpNames = readMCPNamesFromConfiguredPaths()
        availableSkillNames = sortedUnique(config.agent.skills + skillNames)
        availableMCPNames = sortedUnique(extractDisabledNames(prefix: "mcp:") + mcpNames)
        statusMessage = "已刷新可选配置项。"
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
        let system = config.agent.system.trimmingCharacters(in: .whitespacesAndNewlines)
        if !system.isEmpty {
            messages.append(ProviderChatMessage(role: "system", content: system))
        }
        for message in selectedConversation?.messages ?? [] {
            let role = message.role == .user ? "user" : "assistant"
            messages.append(ProviderChatMessage(role: role, content: message.text))
        }
        return messages
    }

    private func readSkillNamesFromConfiguredPaths() -> [String] {
        scanManifestNames(in: config.paths.skills, manifestFileName: "skill.toml")
    }

    private func readMCPNamesFromConfiguredPaths() -> [String] {
        scanManifestNames(in: config.paths.mcp, manifestFileName: "mcp.toml")
    }

    private func scanManifestNames(in paths: [String], manifestFileName: String) -> [String] {
        paths.flatMap { path in
            readManifestNames(in: resolveConfigPath(path), manifestFileName: manifestFileName)
        }
    }

    private func readManifestNames(in root: URL, manifestFileName: String) -> [String] {
        let fileManager = FileManager.default
        let directManifest = root.appendingPathComponent(manifestFileName)
        if fileManager.fileExists(atPath: directManifest.path),
           let name = readManifestName(from: directManifest) {
            return [name]
        }
        guard let children = try? fileManager.contentsOfDirectory(
            at: root,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else {
            return []
        }
        return children.compactMap { child in
            let manifest = child.appendingPathComponent(manifestFileName)
            return fileManager.fileExists(atPath: manifest.path) ? readManifestName(from: manifest) : nil
        }
    }

    private func readManifestName(from url: URL) -> String? {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            return nil
        }
        for rawLine in text.components(separatedBy: .newlines) {
            let line = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
            guard line.hasPrefix("name"), let equalIndex = line.firstIndex(of: "=") else {
                continue
            }
            return cleanTomlString(String(line[line.index(after: equalIndex)...]))
        }
        return nil
    }

    private func cleanTomlString(_ rawValue: String) -> String {
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.hasPrefix("\""), value.hasSuffix("\""),
           let data = value.data(using: .utf8),
           let text = try? JSONDecoder().decode(String.self, from: data) {
            return text
        }
        if value.hasPrefix("'"), value.hasSuffix("'") {
            return String(value.dropFirst().dropLast())
        }
        return value
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
