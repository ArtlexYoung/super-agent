import SwiftUI

@main
struct MacChatApp: App {
    @StateObject private var chatStore = ChatStore()

    var body: some Scene {
        WindowGroup {
            ChatPage()
                .environmentObject(chatStore)
                .frame(minWidth: 1120, minHeight: 720)
        }
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("新建对话") {
                    chatStore.createNewConversation()
                }
                .keyboardShortcut("n")
            }
        }
    }
}
