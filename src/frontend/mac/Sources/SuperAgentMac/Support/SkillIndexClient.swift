import Foundation

private struct SkillIndexDocument: Decodable {
    var skills: [SkillIndexEntry]
}

private struct SkillIndexEntry: Decodable {
    var key: String
    var name: String
    var kind: String
    var agentCreated: Bool
    var agentCanUpdate: Bool
    var freshness: Double
    var functionGroup: String
    var freshnessUpdatedAt: String
    var callCount: Int
    var successCount: Int
    var sameFunctionSuccessfulFollowups: Int

    func makeChoice() -> SkillManifestChoice {
        SkillManifestChoice(
            key: key,
            name: name,
            kind: kind,
            agentCreated: agentCreated,
            agentCanUpdate: agentCanUpdate,
            freshness: freshness,
            functionGroup: functionGroup,
            freshnessUpdatedAt: freshnessUpdatedAt,
            callCount: callCount,
            successCount: successCount,
            replacementCount: sameFunctionSuccessfulFollowups,
            hasRuntimeStats: callCount > 0 || !freshnessUpdatedAt.isEmpty
        )
    }
}

enum SkillIndexClient {
    static func readSkillChoices(configText: String) async throws -> [SkillManifestChoice] {
        try await Task.detached(priority: .userInitiated) {
            try readSkillChoicesFromProcess(configText: configText)
        }.value
    }

    private static func readSkillChoicesFromProcess(configText: String) throws -> [SkillManifestChoice] {
        let temporaryDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("super-agent-index-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: temporaryDirectory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: temporaryDirectory) }

        let configURL = temporaryDirectory.appendingPathComponent("agent.toml")
        try configText.write(to: configURL, atomically: true, encoding: .utf8)

        let process = Process()
        let standardOutput = Pipe()
        let standardError = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [
            runtimeCommand(),
            "skills",
            "index",
            "--config", configURL.path,
            "--output", "json",
        ]
        process.standardOutput = standardOutput
        process.standardError = standardError

        do {
            try process.run()
        } catch {
            throw AgentRuntimeClientError.commandFailed(error.localizedDescription)
        }
        let outputData = standardOutput.fileHandleForReading.readDataToEndOfFile()
        let errorData = standardError.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()

        guard process.terminationStatus == 0 else {
            let text = String(data: errorData, encoding: .utf8) ?? ""
            throw AgentRuntimeClientError.commandFailed(
                text.trimmingCharacters(in: .whitespacesAndNewlines)
            )
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let document = try decoder.decode(SkillIndexDocument.self, from: outputData)
        return document.skills.map { $0.makeChoice() }
    }

    private static func runtimeCommand() -> String {
        let configured = ProcessInfo.processInfo.environment["SUPER_AGENT_COMMAND"]?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if let configured, !configured.isEmpty {
            return configured
        }
        return "super-agent"
    }
}
