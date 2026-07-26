import Foundation

struct AgentTomlConfig: Codable, Equatable {
    var agent: AgentTomlAgentSection
    var model: AgentTomlModelSection
    var paths: AgentTomlPathsSection
    var storage: AgentTomlStorageSection

    static let defaultConfig = AgentTomlConfig(
        agent: AgentTomlAgentSection(
            name: "super-agent",
            system: "You are a concise, helpful agent.",
            workflow: "direct",
            memory: "default",
            skills: ["echo"],
            useFeatures: ["skill"],
            disableNames: [],
            maxAgentChainDepth: nil
        ),
        model: AgentTomlModelSection(
            provider: "auto",
            model: "",
            baseURL: "",
            apiKeyEnv: ""
        ),
        paths: AgentTomlPathsSection(
            skills: ["skills"]
        ),
        storage: AgentTomlStorageSection(
            backend: "jsonl",
            path: ".super-agent",
            urlEnv: ""
        )
    )

    var modelSummary: String {
        model.model.isEmpty ? model.provider : "\(model.provider) / \(model.model)"
    }
}

struct AgentTomlAgentSection: Codable, Equatable {
    var name: String
    var system: String
    var workflow: String
    var memory: String
    var skills: [String]
    var useFeatures: [String]
    var disableNames: [String]
    var maxAgentChainDepth: Int?
}

struct AgentTomlModelSection: Codable, Equatable {
    var provider: String
    var model: String
    var baseURL: String
    var apiKeyEnv: String
}

struct ModelProfile: Identifiable, Codable, Equatable {
    let id: UUID
    var title: String
    var settings: AgentTomlModelSection

    init(id: UUID = UUID(), title: String, settings: AgentTomlModelSection) {
        self.id = id
        self.title = title
        self.settings = settings
    }
}

struct SkillManifestChoice: Identifiable, Equatable, Sendable {
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
    var replacementCount: Int
    var hasRuntimeStats: Bool

    var id: String { key }

    init(
        key: String,
        name: String,
        capability: String,
        agentCreated: Bool,
        agentCanUpdate: Bool,
        freshness: Double = 70,
        functionGroup: String = "",
        freshnessUpdatedAt: String = "",
        callCount: Int = 0,
        successCount: Int = 0,
        replacementCount: Int = 0,
        hasRuntimeStats: Bool = false
    ) {
        self.key = key
        self.name = name
        self.capability = capability
        self.agentCreated = agentCreated
        self.agentCanUpdate = agentCanUpdate
        self.freshness = freshness
        self.functionGroup = functionGroup
        self.freshnessUpdatedAt = freshnessUpdatedAt
        self.callCount = callCount
        self.successCount = successCount
        self.replacementCount = replacementCount
        self.hasRuntimeStats = hasRuntimeStats
    }
}

struct AgentTomlPathsSection: Codable, Equatable {
    var skills: [String]
}

struct AgentTomlStorageSection: Codable, Equatable {
    var backend: String
    var path: String
    var urlEnv: String
}
