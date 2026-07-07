import Foundation

struct AgentTomlConfig: Codable, Equatable {
    var agent: AgentTomlAgentSection
    var model: AgentTomlModelSection
    var paths: AgentTomlPathsSection

    static let defaultConfig = AgentTomlConfig(
        agent: AgentTomlAgentSection(
            name: "super-agent",
            system: "You are a concise, helpful agent.",
            workflow: "direct",
            skills: ["echo"],
            useFeatures: ["skill", "memory", "mcp"],
            disableNames: [],
            maxAgentChainDepth: nil
        ),
        model: AgentTomlModelSection(
            provider: "mock",
            model: "mock",
            baseURL: "",
            apiKeyEnv: ""
        ),
        paths: AgentTomlPathsSection(
            skills: ["skills"],
            mcp: ["mcp"],
            memory: ".super-agent/memory"
        )
    )

    var modelSummary: String {
        "\(model.provider) / \(model.model)"
    }
}

struct AgentTomlAgentSection: Codable, Equatable {
    var name: String
    var system: String
    var workflow: String
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

struct SkillManifestChoice: Identifiable, Equatable {
    var name: String
    var agentCreated: Bool
    var agentCanUpdate: Bool

    var id: String { name }
}

struct AgentTomlPathsSection: Codable, Equatable {
    var skills: [String]
    var mcp: [String]
    var memory: String
}
