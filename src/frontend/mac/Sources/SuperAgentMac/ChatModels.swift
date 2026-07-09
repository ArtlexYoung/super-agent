import Foundation

enum ChatMessageRole: String, Codable, CaseIterable, Identifiable {
    case user = "用户"
    case assistant = "助手"

    var id: String { rawValue }
}

struct ChatMessage: Identifiable, Codable, Equatable {
    let id: UUID
    let role: ChatMessageRole
    var text: String
    let createdAt: Date

    init(id: UUID = UUID(), role: ChatMessageRole, text: String, createdAt: Date = Date()) {
        self.id = id
        self.role = role
        self.text = text
        self.createdAt = createdAt
    }
}

struct ChatConversation: Identifiable, Codable, Equatable {
    let id: UUID
    var title: String
    var messages: [ChatMessage]
    var agentRuns: [AgentRunNode]
    var updatedAt: Date

    init(
        id: UUID = UUID(),
        title: String = "新对话",
        messages: [ChatMessage] = [],
        agentRuns: [AgentRunNode] = [],
        updatedAt: Date = Date()
    ) {
        self.id = id
        self.title = title
        self.messages = messages
        self.agentRuns = agentRuns
        self.updatedAt = updatedAt
    }

    private enum CodingKeys: String, CodingKey {
        case id
        case title
        case messages
        case agentRuns
        case updatedAt
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(UUID.self, forKey: .id)
        title = try values.decode(String.self, forKey: .title)
        messages = try values.decode([ChatMessage].self, forKey: .messages)
        agentRuns = try values.decodeIfPresent([AgentRunNode].self, forKey: .agentRuns) ?? []
        updatedAt = try values.decode(Date.self, forKey: .updatedAt)
    }
}

struct AgentRunNode: Identifiable, Codable, Equatable {
    let id: UUID
    var agentName: String
    var title: String
    var prompt: String
    var response: String
    var createdAt: Date
    var updatedAt: Date
    var createdByAgent: Bool
    var children: [AgentRunNode]

    init(
        id: UUID = UUID(),
        agentName: String,
        title: String,
        prompt: String,
        response: String,
        createdAt: Date = Date(),
        updatedAt: Date = Date(),
        createdByAgent: Bool = false,
        children: [AgentRunNode] = []
    ) {
        self.id = id
        self.agentName = agentName
        self.title = title
        self.prompt = prompt
        self.response = response
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.createdByAgent = createdByAgent
        self.children = children
    }
}
