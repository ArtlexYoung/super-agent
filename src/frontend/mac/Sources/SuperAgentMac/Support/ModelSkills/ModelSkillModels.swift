import Foundation

struct RuntimeModelSkillProfile: Decodable, Equatable, Sendable {
    var key: String
    var name: String
    var description: String
    var version: String
    var provider: String
    var model: String
    var baseUrl: String?
    var apiKeyEnv: String?
    var supports: [String]
    var purposes: [String]
    var strengths: [String]
    var triggers: [String]
    var isDefault: Bool
    var qualityScore: Double?
    var expectedLatencyMs: Int?
    var inputCostPerMillion: Double?
    var outputCostPerMillion: Double?
    var source: String
    var skillKey: String?
    var agentCreated: Bool
    var agentCanUpdate: Bool
    var agentCanUpdateConnection: Bool
    var ready: Bool

    private enum CodingKeys: String, CodingKey {
        case key, name, description, version, provider, model
        case baseUrl = "base_url"
        case apiKeyEnv = "api_key_env"
        case supports, purposes, strengths, triggers
        case isDefault = "default"
        case qualityScore = "quality_score"
        case expectedLatencyMs = "expected_latency_ms"
        case inputCostPerMillion = "input_cost_per_million"
        case outputCostPerMillion = "output_cost_per_million"
        case source
        case skillKey = "skill_key"
        case agentCreated = "agent_created"
        case agentCanUpdate = "agent_can_update"
        case agentCanUpdateConnection = "agent_can_update_connection"
        case ready
    }
}

struct RuntimeModelSkillList: Decodable, Sendable {
    var schemaVersion: Int
    var models: [RuntimeModelSkillProfile]
}

struct RuntimeModelSkillChange: Decodable, Sendable {
    var schemaVersion: Int
    var action: String
    var model: RuntimeModelSkillProfile
}

struct ModelSkillDraft: Identifiable, Equatable {
    let id: UUID
    var previousName: String
    var name: String
    var description: String
    var provider: String
    var model: String
    var baseUrl: String
    var apiKeyEnv: String
    var persistedApiKeyEnv: String
    var credential: String
    var supports: [String]
    var purposes: [String]
    var strengthsText: String
    var triggersText: String
    var isDefault: Bool
    var agentCanUpdate: Bool
    var agentCanUpdateConnection: Bool
    var qualityScore: Double
    var expectedLatencyMs: Int
    var inputCostPerMillion: Double
    var outputCostPerMillion: Double
    var source: String
    var version: String
    var ready: Bool
    var credentialStored: Bool

    init(profile: RuntimeModelSkillProfile) {
        id = UUID()
        previousName = profile.source == "skill" ? profile.name : ""
        name = profile.name
        description = profile.description
        provider = profile.provider
        model = profile.model
        baseUrl = profile.baseUrl ?? ""
        apiKeyEnv = profile.apiKeyEnv ?? ""
        persistedApiKeyEnv = profile.apiKeyEnv ?? ""
        credential = ""
        supports = profile.supports
        purposes = profile.purposes
        strengthsText = profile.strengths.joined(separator: ", ")
        triggersText = profile.triggers.joined(separator: ", ")
        isDefault = profile.isDefault
        agentCanUpdate = profile.agentCanUpdate
        agentCanUpdateConnection = profile.agentCanUpdateConnection
        qualityScore = profile.qualityScore ?? 0.5
        expectedLatencyMs = profile.expectedLatencyMs ?? 0
        inputCostPerMillion = profile.inputCostPerMillion ?? 0
        outputCostPerMillion = profile.outputCostPerMillion ?? 0
        source = profile.source
        version = profile.version
        ready = profile.ready
        credentialStored = ModelCredentialStore.contains(profile.apiKeyEnv ?? "")
    }

    init(name: String) {
        id = UUID()
        previousName = ""
        self.name = name
        description = "User-configured model Skill."
        provider = "openai-compatible"
        model = ""
        baseUrl = ""
        apiKeyEnv = "SUPER_AGENT_\(modelEnvironmentNamePart(name))_API_KEY"
        persistedApiKeyEnv = ""
        credential = ""
        supports = ["text"]
        purposes = ["answer"]
        strengthsText = ""
        triggersText = ""
        isDefault = false
        agentCanUpdate = true
        agentCanUpdateConnection = false
        qualityScore = 0.5
        expectedLatencyMs = 0
        inputCostPerMillion = 0
        outputCostPerMillion = 0
        source = "draft"
        version = "new"
        ready = false
        credentialStored = false
    }

    var isPersisted: Bool {
        source == "skill" && !previousName.isEmpty
    }

    var summary: String {
        model.isEmpty ? provider : "\(provider) / \(model)"
    }

    func makeSaveRequest() -> ModelSkillSaveRequest {
        ModelSkillSaveRequest(
            name: name,
            previousName: previousName,
            description: description,
            provider: provider,
            model: model,
            baseUrl: baseUrl,
            apiKeyEnv: apiKeyEnv,
            supports: supports,
            purposes: purposes,
            strengths: splitModelTags(strengthsText),
            triggers: splitModelTags(triggersText),
            isDefault: isDefault,
            agentCanUpdate: agentCanUpdate,
            agentCanUpdateConnection: agentCanUpdateConnection,
            qualityScore: qualityScore,
            expectedLatencyMs: expectedLatencyMs,
            inputCostPerMillion: inputCostPerMillion,
            outputCostPerMillion: outputCostPerMillion
        )
    }
}

struct ModelSkillSaveRequest: Encodable, Sendable {
    var name: String
    var previousName: String
    var description: String
    var provider: String
    var model: String
    var baseUrl: String
    var apiKeyEnv: String
    var supports: [String]
    var purposes: [String]
    var strengths: [String]
    var triggers: [String]
    var isDefault: Bool
    var agentCanUpdate: Bool
    var agentCanUpdateConnection: Bool
    var qualityScore: Double
    var expectedLatencyMs: Int
    var inputCostPerMillion: Double
    var outputCostPerMillion: Double

    private enum CodingKeys: String, CodingKey {
        case name, description, provider, model, supports, purposes, strengths, triggers
        case previousName = "previous_name"
        case baseUrl = "base_url"
        case apiKeyEnv = "api_key_env"
        case isDefault = "default"
        case agentCanUpdate = "agent_can_update"
        case agentCanUpdateConnection = "agent_can_update_connection"
        case qualityScore = "quality_score"
        case expectedLatencyMs = "expected_latency_ms"
        case inputCostPerMillion = "input_cost_per_million"
        case outputCostPerMillion = "output_cost_per_million"
    }
}

func splitModelTags(_ text: String) -> [String] {
    var seen: Set<String> = []
    return text
        .split(separator: ",")
        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
        .filter { !$0.isEmpty && seen.insert($0).inserted }
}

private func modelEnvironmentNamePart(_ name: String) -> String {
    name.uppercased().map { character in
        character.isLetter || character.isNumber ? character : "_"
    }.reduce(into: "") { $0.append($1) }
}
