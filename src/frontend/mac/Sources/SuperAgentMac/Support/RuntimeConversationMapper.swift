import Foundation

func makeChatConversation(from stored: RuntimeConversation) -> ChatConversation {
    let messages = stored.messages.map { message in
        ChatMessage(
            id: message.messageId,
            role: message.role == "user" ? .user : .assistant,
            text: message.content,
            createdAt: message.createdAt
        )
    }
    let promptsByRunID = stored.messages.reduce(into: [String: String]()) { prompts, message in
        if message.role == "user", !message.runId.isEmpty {
            prompts[message.runId] = message.content
        }
    }
    let agentRuns = stored.messages.compactMap { message -> AgentRunNode? in
        guard message.role == "assistant", let result = message.runResult else {
            return nil
        }
        return makeAgentRunNode(
            agentName: stored.agentName,
            prompt: promptsByRunID[result.runId] ?? "",
            result: result,
            createdAt: message.createdAt
        )
    }
    return ChatConversation(
        id: stored.conversationId,
        title: stored.title.isEmpty ? "新对话" : stored.title,
        messages: messages,
        agentRuns: agentRuns,
        updatedAt: stored.updatedAt
    )
}

private func makeAgentRunNode(
    agentName: String,
    prompt: String,
    result: AgentRuntimeResult,
    createdAt: Date
) -> AgentRunNode {
    AgentRunNode(
        id: result.runId,
        runID: result.runId,
        agentName: agentName,
        title: String(prompt.prefix(24)),
        prompt: prompt,
        response: result.text,
        createdAt: createdAt,
        updatedAt: createdAt,
        createdByAgent: false,
        children: (result.subagentResults ?? []).map {
            makeSubagentRunNode($0, createdAt: createdAt)
        }
    )
}

private func makeSubagentRunNode(
    _ result: AgentRuntimeSubagentResult,
    createdAt: Date
) -> AgentRunNode {
    AgentRunNode(
        id: result.runId,
        runID: result.runId,
        agentName: result.name,
        title: result.description.isEmpty ? result.name : result.description,
        prompt: result.prompt,
        response: result.text,
        createdAt: createdAt,
        updatedAt: createdAt,
        createdByAgent: result.createdByAgent,
        children: (result.subagentResults ?? []).map {
            makeSubagentRunNode($0, createdAt: createdAt)
        }
    )
}
