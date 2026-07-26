import Foundation

struct StoredMacAppState: Codable {
    var selectedConversationID: String?
    var config: AgentTomlConfig
    var configFilePath: String?
    var modelProfiles: [ModelProfile]
    var selectedModelProfileID: UUID?
}

private struct StoredMacConfig: Codable {
    var selectedConversationID: String?
    var config: AgentTomlConfig
    var configFilePath: String?
    var modelProfiles: [ModelProfile]
    var selectedModelProfileID: UUID?
}

enum MacAppStorage {
    static func loadState() throws -> StoredMacAppState? {
        guard FileManager.default.fileExists(atPath: try configURL().path) else {
            return nil
        }
        let data = try Data(contentsOf: configURL())
        let stored = try JSONDecoder.storageDecoder.decode(StoredMacConfig.self, from: data)
        return StoredMacAppState(
            selectedConversationID: stored.selectedConversationID,
            config: stored.config,
            configFilePath: stored.configFilePath,
            modelProfiles: stored.modelProfiles,
            selectedModelProfileID: stored.selectedModelProfileID
        )
    }

    static func saveState(_ state: StoredMacAppState) throws {
        try createBaseDirectory()
        try saveConfig(for: state)
    }

    private static func saveConfig(for state: StoredMacAppState) throws {
        let storedConfig = StoredMacConfig(
            selectedConversationID: state.selectedConversationID,
            config: state.config,
            configFilePath: state.configFilePath,
            modelProfiles: state.modelProfiles,
            selectedModelProfileID: state.selectedModelProfileID
        )
        let data = try JSONEncoder.storageEncoder.encode(storedConfig)
        try data.write(to: configURL(), options: .atomic)
    }

    private static func createBaseDirectory() throws {
        try FileManager.default.createDirectory(
            at: baseURL(),
            withIntermediateDirectories: true
        )
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

    private static func configURL() throws -> URL {
        try baseURL().appendingPathComponent("config.json")
    }

}

private extension JSONEncoder {
    static var storageEncoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return encoder
    }
}

private extension JSONDecoder {
    static var storageDecoder: JSONDecoder {
        let decoder = JSONDecoder()
        return decoder
    }
}
