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
    var updatedAt: Date

    init(id: UUID = UUID(), title: String = "新对话", messages: [ChatMessage] = [], updatedAt: Date = Date()) {
        self.id = id
        self.title = title
        self.messages = messages
        self.updatedAt = updatedAt
    }
}
