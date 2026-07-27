import Foundation

private struct SkillIndexDocument: Decodable {
    var skills: [SkillIndexEntry]
}

private struct SkillIndexEntry: Decodable {
    var key: String
    var name: String
    var capability: String
    var agentCreated: Bool
    var agentCanUpdate: Bool
    var freshness: Double
    var functionGroup: String
    var freshnessUpdatedAt: String
    var callCount: Int
    var successCount: Int
    var sameFunctionSuccessfulFollowups: Int

}

enum SkillIndexClient {
    static func readSkillChoices(configText: String) async throws -> [SkillManifestChoice] {
        try await Task.detached(priority: .userInitiated) {
            let data = try AgentRuntimeClient.runRuntimeCommand(
                configText: configText,
                arguments: ["skills", "index", "--output", "json"]
            )
            let document = try AgentRuntimeClient.decodeRuntimeJSON(
                SkillIndexDocument.self,
                from: data
            )
            return makeSkillChoices(from: document.skills)
        }.value
    }

    private static func makeSkillChoices(
        from entries: [SkillIndexEntry]
    ) -> [SkillManifestChoice] {
        var choices: [SkillManifestChoice] = []
        choices.reserveCapacity(entries.count)
        for entry in entries {
            let choice = SkillManifestChoice(
                key: entry.key,
                name: entry.name,
                capability: entry.capability,
                agentCreated: entry.agentCreated,
                agentCanUpdate: entry.agentCanUpdate,
                freshness: entry.freshness,
                functionGroup: entry.functionGroup,
                freshnessUpdatedAt: entry.freshnessUpdatedAt,
                callCount: entry.callCount,
                successCount: entry.successCount,
                replacementCount: entry.sameFunctionSuccessfulFollowups,
                hasRuntimeStats: entry.callCount > 0 || !entry.freshnessUpdatedAt.isEmpty
            )
            choices.append(choice)
        }
        return choices
    }
}
