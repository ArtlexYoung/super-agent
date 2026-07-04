import SwiftUI

struct ChatPage: View {
    var body: some View {
        HStack(spacing: 0) {
            ConversationListView()
                .frame(width: 260)

            Divider()

            ChatMessagesView()
                .frame(minWidth: 520)

            Divider()

            ConfigPanelView()
                .frame(width: 320)
        }
        .background(
            LinearGradient(
                colors: [Color(nsColor: .windowBackgroundColor), Color.blue.opacity(0.05)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
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

            HStack {
                Button("删除") {
                    chatStore.deleteSelectedConversation()
                }
                Button("清空消息") {
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
            Text("\(conversation.messages.count) 条消息")
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
                    LazyVStack(spacing: 14) {
                        ForEach(chatStore.selectedConversation?.messages ?? []) { message in
                            MessageBubbleView(message: message)
                                .id(message.id)
                        }
                    }
                    .padding(24)
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

            if let warningMessage = chatStore.warningMessage {
                Text(warningMessage)
                    .font(.caption)
                    .foregroundStyle(.orange)
            } else {
                Text("当前对话会先使用本地模拟回复，后续可接入 Python runtime。")
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
                Text(message.role.rawValue)
                    .font(.caption.weight(.semibold))
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
                .frame(height: 92)
                .padding(8)
                .background(Color(nsColor: .textBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))

            HStack {
                Text("Enter 换行，点击发送提交。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("发送") {
                    chatStore.sendDraftMessage()
                }
                .keyboardShortcut(.return, modifiers: [.command])
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(20)
    }
}

private struct ConfigPanelView: View {
    @EnvironmentObject private var chatStore: ChatStore

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 4) {
                Text("配置")
                    .font(.title2.weight(.semibold))
                Text("运行参数先保存在内存中。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Form {
                TextField("服务地址", text: $chatStore.config.endpoint)
                TextField("模型名称", text: $chatStore.config.modelName)
                SecureField("API Key", text: $chatStore.config.apiKey)

                VStack(alignment: .leading) {
                    Text("温度 \(chatStore.config.temperature, specifier: "%.1f")")
                    Slider(value: $chatStore.config.temperature, in: 0...1, step: 0.1)
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text("系统提示词")
                    TextEditor(text: $chatStore.config.systemPrompt)
                        .frame(height: 120)
                        .padding(6)
                        .background(Color(nsColor: .textBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                }
            }
            .formStyle(.grouped)

            Spacer()
        }
        .padding(20)
    }
}
