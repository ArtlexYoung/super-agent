import Combine
import Foundation

@MainActor
final class ChatStore: ObservableObject {
    @Published private(set) var conversations: [ChatConversation]
    @Published private(set) var selectedConversationID: UUID?
    @Published var config: MacAgentConfig
    @Published var draftMessage: String
    @Published var warningMessage: String?

    init() {
        let firstConversation = ChatConversation(title: "欢迎对话")
        conversations = [firstConversation]
        selectedConversationID = firstConversation.id
        config = .defaultConfig
        draftMessage = ""
    }

    var selectedConversation: ChatConversation? {
        guard let selectedConversationID else {
            return conversations.first
        }
        return conversations.first { $0.id == selectedConversationID }
    }

    func createNewConversation() {
        let conversation = ChatConversation()
        conversations.insert(conversation, at: 0)
        selectedConversationID = conversation.id
        warningMessage = nil
    }

    func selectConversation(_ id: UUID) {
        selectedConversationID = id
        warningMessage = nil
    }

    func renameSelectedConversation(to title: String) {
        let cleanTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanTitle.isEmpty else {
            return
        }
        updateSelectedConversation { conversation in
            conversation.title = cleanTitle
        }
    }

    func deleteSelectedConversation() {
        guard let selectedConversationID else {
            return
        }
        deleteConversation(selectedConversationID)
    }

    func deleteConversation(_ id: UUID) {
        conversations.removeAll { $0.id == id }
        if conversations.isEmpty {
            createNewConversation()
            return
        }
        if selectedConversationID == id {
            selectedConversationID = conversations.first?.id
        }
        warningMessage = nil
    }

    func clearSelectedConversationMessages() {
        updateSelectedConversation { conversation in
            conversation.messages.removeAll()
        }
    }

    func sendDraftMessage() {
        let text = draftMessage.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            warningMessage = "请输入消息后再发送。"
            return
        }
        warningMessage = nil
        draftMessage = ""
        appendMessageToSelectedConversation(ChatMessage(role: .user, text: text))
        appendMessageToSelectedConversation(ChatMessage(role: .assistant, text: makeLocalAssistantReply(for: text)))
    }

    private func appendMessageToSelectedConversation(_ message: ChatMessage) {
        updateSelectedConversation { conversation in
            if conversation.messages.isEmpty && message.role == .user {
                conversation.title = String(message.text.prefix(18))
            }
            conversation.messages.append(message)
        }
    }

    private func updateSelectedConversation(_ change: (inout ChatConversation) -> Void) {
        guard let selectedConversationID,
              let index = conversations.firstIndex(where: { $0.id == selectedConversationID }) else {
            return
        }
        change(&conversations[index])
        conversations[index].updatedAt = Date()
    }

    private func makeLocalAssistantReply(for userText: String) -> String {
        """
        已收到：「\(userText)」

        当前配置：
        - endpoint: \(config.endpoint)
        - model: \(config.modelName)
        - temperature: \(String(format: "%.1f", config.temperature))

        这里先返回本地模拟回复。后续可以把 sendDraftMessage() 替换成调用 super-agent 后端或本地进程。
        """
    }
}
