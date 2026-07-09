import SwiftUI

private enum MainPage: String, CaseIterable, Identifiable {
    case chat = "对话"
    case config = "配置"

    var id: String { rawValue }

    var iconName: String {
        switch self {
        case .chat:
            return "bubble.left.and.bubble.right"
        case .config:
            return "slider.horizontal.3"
        }
    }
}

struct ChatPage: View {
    @State private var selectedPage: MainPage = .chat

    var body: some View {
        HStack(spacing: 0) {
            AppSidebarView(selectedPage: $selectedPage)
                .frame(width: 164)

            Divider()

            switch selectedPage {
            case .chat:
                ChatWorkspaceView()
            case .config:
                ConfigPageView()
            }
        }
        .background(
            LinearGradient(
                colors: [Color(nsColor: .windowBackgroundColor), Color.teal.opacity(0.06)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
    }
}

private struct AppSidebarView: View {
    @Binding var selectedPage: MainPage

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Super Agent")
                    .font(.title3.weight(.semibold))
                Text("轻量助手")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 14)
            .padding(.top, 18)

            VStack(spacing: 6) {
                ForEach(MainPage.allCases) { page in
                    SidebarItemView(
                        page: page,
                        isSelected: page == selectedPage
                    ) {
                        selectedPage = page
                    }
                }
            }
            .padding(.horizontal, 10)

            Spacer()
        }
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.72))
    }
}

private struct SidebarItemView: View {
    let page: MainPage
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: page.iconName)
                    .frame(width: 18)
                Text(page.rawValue)
                    .font(.body.weight(.medium))
                Spacer()
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .foregroundStyle(isSelected ? Color.accentColor : Color.primary)
            .background(isSelected ? Color.accentColor.opacity(0.14) : Color.clear)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

private struct ChatWorkspaceView: View {
    var body: some View {
        HStack(spacing: 0) {
            ConversationListView()
                .frame(width: 300)

            Divider()

            ChatMessagesView()
                .frame(minWidth: 680)
        }
    }
}

private struct ConversationListView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Super Agent")
                        .font(.title2.weight(.semibold))
                    Text("对话管理")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    chatStore.createNewConversation()
                } label: {
                    Image(systemName: "plus")
                }
                .help("新建对话")
            }

            List(chatStore.conversations) { conversation in
                ConversationRowView(conversation: conversation)
                    .contentShape(Rectangle())
                    .onTapGesture {
                        chatStore.selectConversation(conversation.id)
                    }
                    .listRowBackground(
                        conversation.id == chatStore.selectedConversationID
                            ? Color.accentColor.opacity(0.14)
                            : Color.clear
                    )
            }
            .scrollContentBackground(.hidden)

            Divider()

            AgentRunTreeView()

            HStack {
                Button("删除") {
                    chatStore.deleteSelectedConversation()
                }
                Button("清空") {
                    chatStore.clearSelectedConversationMessages()
                }
            }
            .buttonStyle(.bordered)
        }
        .padding(18)
    }
}

private struct ConversationRowView: View {
    let conversation: ChatConversation

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(conversation.title)
                .font(.headline)
                .lineLimit(1)
            HStack(spacing: 8) {
                Text("\(conversation.messages.count) 条")
                Text(conversation.updatedAt, style: .relative)
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .padding(.vertical, 8)
    }
}

private struct ChatMessagesView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        VStack(spacing: 0) {
            ChatHeaderView()

            Divider()

            ScrollViewReader { scrollProxy in
                ScrollView {
                    if let node = chatStore.selectedAgentRunNode {
                        AgentRunDetailView(node: node)
                            .padding(24)
                    } else if (chatStore.selectedConversation?.messages ?? []).isEmpty {
                        EmptyConversationView()
                            .padding(.top, 80)
                    } else {
                        LazyVStack(spacing: 14) {
                            ForEach(chatStore.selectedConversation?.messages ?? []) { message in
                                MessageBubbleView(message: message)
                                    .id(message.id)
                            }
                        }
                        .padding(24)
                    }
                }
                .onChange(of: chatStore.selectedConversation?.messages.count ?? 0) { _ in
                    guard let lastMessage = chatStore.selectedConversation?.messages.last else {
                        return
                    }
                    withAnimation(.easeOut(duration: 0.2)) {
                        scrollProxy.scrollTo(lastMessage.id, anchor: .bottom)
                    }
                }
            }

            Divider()

            MessageInputView()
        }
    }
}

private struct AgentRunTreeView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("运行树")
                    .font(.headline)
                Spacer()
                Button {
                    chatStore.selectAgentRunNode(nil)
                } label: {
                    Image(systemName: "rectangle.stack")
                }
                .buttonStyle(.plain)
                .help("返回完整对话")
            }

            let runs = chatStore.selectedConversation?.agentRuns ?? []
            if runs.isEmpty {
                Text("发送消息后会生成运行节点。子 agent 结果会像文件树一样挂在父节点下。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 3) {
                        ForEach(runs) { node in
                            AgentRunNodeRowView(node: node, depth: 0)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 220)
            }
        }
    }
}

private struct AgentRunNodeRowView: View {
    @EnvironmentObject private var chatStore: ChatStore
    let node: AgentRunNode
    let depth: Int

    private var isSelected: Bool {
        chatStore.selectedAgentRunNodeID == node.id
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Button {
                chatStore.selectAgentRunNode(node.id)
            } label: {
                HStack(spacing: 7) {
                    Spacer()
                        .frame(width: CGFloat(depth * 14))
                    Image(systemName: node.children.isEmpty ? "doc.text" : "folder")
                        .frame(width: 16)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(node.title.isEmpty ? node.agentName : node.title)
                            .lineLimit(1)
                        Text(node.agentName)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                    if node.createdByAgent {
                        Text("自建")
                            .font(.caption2.weight(.semibold))
                            .padding(.horizontal, 5)
                            .padding(.vertical, 2)
                            .background(Color.accentColor.opacity(0.14))
                            .clipShape(RoundedRectangle(cornerRadius: 5, style: .continuous))
                    }
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 6)
                .background(isSelected ? Color.accentColor.opacity(0.14) : Color.clear)
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            }
            .buttonStyle(.plain)
            .help("查看 \(node.agentName) 的运行对话")

            ForEach(node.children) { child in
                AgentRunNodeRowView(node: child, depth: depth + 1)
            }
        }
    }
}

private struct AgentRunDetailView: View {
    @EnvironmentObject private var chatStore: ChatStore
    let node: AgentRunNode

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(node.title.isEmpty ? "运行对话" : node.title)
                        .font(.title3.weight(.semibold))
                    HStack(spacing: 8) {
                        Text(node.agentName)
                        if node.createdByAgent {
                            Text("agent 自建")
                        }
                        Text(node.updatedAt, style: .relative)
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                Spacer()
                Button("完整对话") {
                    chatStore.selectAgentRunNode(nil)
                }
                .buttonStyle(.bordered)
            }

            RunTextBlockView(title: "输入", text: node.prompt, isResponse: false)
            RunTextBlockView(title: "输出", text: node.response, isResponse: true)

            if !node.children.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("子 agent")
                        .font(.headline)
                    ForEach(node.children) { child in
                        Button {
                            chatStore.selectAgentRunNode(child.id)
                        } label: {
                            HStack {
                                Image(systemName: "arrow.turn.down.right")
                                Text(child.title.isEmpty ? child.agentName : child.title)
                                Spacer()
                                Text(child.agentName)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .frame(maxWidth: 760, alignment: .leading)
    }
}

private struct RunTextBlockView: View {
    let title: String
    let text: String
    let isResponse: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(text)
                .font(.body)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(14)
        .background(isResponse ? Color(nsColor: .textBackgroundColor) : Color.accentColor.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

private struct EmptyConversationView: View {
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "sparkles.rectangle.stack")
                .font(.system(size: 42))
                .foregroundStyle(.secondary)
            Text("开始一段新对话")
                .font(.title3.weight(.semibold))
            Text("在配置页选择或编辑 agent.toml 后，输入消息即可按模型配置发送。")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}

private struct ChatHeaderView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField(
                "对话标题",
                text: Binding(
                    get: { chatStore.selectedConversation?.title ?? "" },
                    set: { chatStore.renameSelectedConversation(to: $0) }
                )
            )
            .font(.title3.weight(.semibold))
            .textFieldStyle(.plain)

            Text("模型：\(chatStore.config.modelSummary)")
                .font(.caption)
                .foregroundStyle(.secondary)

            if let warningMessage = chatStore.warningMessage {
                Text(warningMessage)
                    .font(.caption)
                    .foregroundStyle(.orange)
            } else if let statusMessage = chatStore.statusMessage {
                Text(statusMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(20)
    }
}

private struct MessageBubbleView: View {
    let message: ChatMessage

    private var isUserMessage: Bool {
        message.role == .user
    }

    var body: some View {
        HStack {
            if isUserMessage {
                Spacer(minLength: 80)
            }

            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text(message.role.rawValue)
                        .font(.caption.weight(.semibold))
                    Spacer()
                    Text(message.createdAt.formatted(date: .omitted, time: .shortened))
                        .font(.caption2)
                }
                .foregroundStyle(.secondary)

                Text(message.text)
                    .font(.body)
                    .textSelection(.enabled)
            }
            .padding(14)
            .background(isUserMessage ? Color.accentColor.opacity(0.18) : Color(nsColor: .textBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .shadow(color: .black.opacity(0.05), radius: 10, y: 4)

            if !isUserMessage {
                Spacer(minLength: 80)
            }
        }
    }
}

private struct MessageInputView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        VStack(spacing: 10) {
            TextEditor(text: $chatStore.draftMessage)
                .font(.body)
                .frame(height: 96)
                .padding(8)
                .background(Color(nsColor: .textBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .disabled(chatStore.isSendingMessage)

            HStack {
                Text("Command + Return 发送。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if chatStore.isSendingMessage {
                    ProgressView()
                        .scaleEffect(0.75)
                }
                Button("发送") {
                    chatStore.sendDraftMessage()
                }
                .keyboardShortcut(.return, modifiers: [.command])
                .buttonStyle(.borderedProminent)
                .disabled(chatStore.isSendingMessage)
            }
        }
        .padding(20)
    }
}
