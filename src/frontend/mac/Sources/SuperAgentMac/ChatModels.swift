import Foundation

enum ChatMessageRole: String, CaseIterable, Identifiable {
    case user = "用户"
    case assistant = "助手"

    var id: String { rawValue }
}

struct ChatMessage: Identifiable, Equatable {
    let id: String
    let role: ChatMessageRole
    var text: String
    let createdAt: Date

    init(
        id: String = UUID().uuidString,
        role: ChatMessageRole,
        text: String,
        createdAt: Date = Date()
    ) {
        self.id = id
        self.role = role
        self.text = text
        self.createdAt = createdAt
    }
}

struct ChatConversation: Identifiable, Equatable {
    let id: String
    var title: String
    var messages: [ChatMessage]
    var agentRuns: [AgentRunNode]
    var updatedAt: Date

    init(
        id: String,
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

}

struct AgentRunNode: Identifiable, Equatable {
    let id: String
    var runID: String?
    var agentName: String
    var title: String
    var prompt: String
    var response: String
    var createdAt: Date
    var updatedAt: Date
    var createdByAgent: Bool
    var children: [AgentRunNode]

    init(
        id: String = UUID().uuidString,
        runID: String? = nil,
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
        self.runID = runID
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
