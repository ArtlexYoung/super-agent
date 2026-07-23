import Foundation

struct StoredMacAppState: Codable {
    var conversations: [ChatConversation]
    var selectedConversationID: UUID?
    var config: AgentTomlConfig
    var configFilePath: String?
    var modelProfiles: [ModelProfile]
    var selectedModelProfileID: UUID?
}

private struct StoredMacIndex: Codable {
    var selectedConversationID: UUID?
    var conversations: [StoredConversationIndexItem]
}

private struct StoredConversationIndexItem: Codable {
    var id: UUID
    var title: String
    var updatedAt: Date
    var messageCount: Int
    var fileName: String
}

private struct StoredMacConfig: Codable {
    var config: AgentTomlConfig
    var configFilePath: String?
    var modelProfiles: [ModelProfile]
    var selectedModelProfileID: UUID?
}

enum MacAppStorage {
    static func loadState() throws -> StoredMacAppState? {
        guard FileManager.default.fileExists(atPath: try indexURL().path) else {
            return nil
        }
        return try loadIndexedState()
    }

    static func saveState(_ state: StoredMacAppState) throws {
        try createBaseDirectories()
        try saveConversations(state.conversations)
        try removeDeletedConversationFiles(keeping: state.conversations.map(\.id))
        try saveIndex(for: state)
        try saveConfig(for: state)
    }

    private static func loadIndexedState() throws -> StoredMacAppState {
        let indexData = try Data(contentsOf: indexURL())
        let index = try JSONDecoder.storageDecoder.decode(StoredMacIndex.self, from: indexData)
        let conversations = index.conversations.compactMap { item in
            try? loadConversation(fileName: item.fileName)
        }
        let storedConfig = try loadStoredConfig()
        return StoredMacAppState(
            conversations: conversations,
            selectedConversationID: index.selectedConversationID,
            config: storedConfig.config,
            configFilePath: storedConfig.configFilePath,
            modelProfiles: storedConfig.modelProfiles,
            selectedModelProfileID: storedConfig.selectedModelProfileID
        )
    }

    private static func loadConversation(fileName: String) throws -> ChatConversation {
        let url = try conversationsDirectoryURL().appendingPathComponent(fileName)
        let data = try Data(contentsOf: url)
        return try JSONDecoder.storageDecoder.decode(ChatConversation.self, from: data)
    }

    private static func loadStoredConfig() throws -> StoredMacConfig {
        let url = try configURL()
        guard FileManager.default.fileExists(atPath: url.path) else {
            let profile = ModelProfile(title: "auto", settings: AgentTomlConfig.defaultConfig.model)
            return StoredMacConfig(
                config: .defaultConfig,
                configFilePath: nil,
                modelProfiles: [profile],
                selectedModelProfileID: profile.id
            )
        }
        let data = try Data(contentsOf: url)
        return try JSONDecoder.storageDecoder.decode(StoredMacConfig.self, from: data)
    }

    private static func saveConversations(_ conversations: [ChatConversation]) throws {
        for conversation in conversations {
            let url = try conversationURL(for: conversation.id)
            let data = try JSONEncoder.storageEncoder.encode(conversation)
            try data.write(to: url, options: .atomic)
        }
    }

    private static func removeDeletedConversationFiles(keeping ids: [UUID]) throws {
        let validFileNames = Set(ids.map { conversationFileName(for: $0) })
        let directory = try conversationsDirectoryURL()
        let files = try FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        )
        for file in files where !validFileNames.contains(file.lastPathComponent) {
            try FileManager.default.removeItem(at: file)
        }
    }

    private static func saveIndex(for state: StoredMacAppState) throws {
        let index = StoredMacIndex(
            selectedConversationID: state.selectedConversationID,
            conversations: state.conversations.map {
                StoredConversationIndexItem(
                    id: $0.id,
                    title: $0.title,
                    updatedAt: $0.updatedAt,
                    messageCount: $0.messages.count,
                    fileName: conversationFileName(for: $0.id)
                )
            }
        )
        let data = try JSONEncoder.storageEncoder.encode(index)
        try data.write(to: indexURL(), options: .atomic)
    }

    private static func saveConfig(for state: StoredMacAppState) throws {
        let storedConfig = StoredMacConfig(
            config: state.config,
            configFilePath: state.configFilePath,
            modelProfiles: state.modelProfiles,
            selectedModelProfileID: state.selectedModelProfileID
        )
        let data = try JSONEncoder.storageEncoder.encode(storedConfig)
        try data.write(to: configURL(), options: .atomic)
    }

    private static func createBaseDirectories() throws {
        try FileManager.default.createDirectory(
            at: conversationsDirectoryURL(),
            withIntermediateDirectories: true
        )
    }

    private static func conversationURL(for id: UUID) throws -> URL {
        try conversationsDirectoryURL().appendingPathComponent(conversationFileName(for: id))
    }

    private static func conversationFileName(for id: UUID) -> String {
        "\(id.uuidString).json"
    }

    private static func baseURL() throws -> URL {
        guard let baseURL = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first else {
            throw CocoaError(.fileNoSuchFile)
        }
        return baseURL.appendingPathComponent("SuperAgentMac", isDirectory: true)
    }

    private static func conversationsDirectoryURL() throws -> URL {
        try baseURL().appendingPathComponent("conversations", isDirectory: true)
    }

    private static func indexURL() throws -> URL {
        try baseURL().appendingPathComponent("index.json")
    }

    private static func configURL() throws -> URL {
        try baseURL().appendingPathComponent("config.json")
    }

}

private extension JSONEncoder {
    static var storageEncoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }
}

private extension JSONDecoder {
    static var storageDecoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}
