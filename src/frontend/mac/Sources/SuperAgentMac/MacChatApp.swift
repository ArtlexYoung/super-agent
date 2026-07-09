import SwiftUI

@main
struct MacChatApp: App {
    @StateObject private var chatStore = ChatStore()

    var body: some Scene {
        WindowGroup {
            ChatPage()
                .environmentObject(chatStore)
                .frame(minWidth: 1310, minHeight: 760)
        }
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("新建对话") {
                    chatStore.createNewConversation()
                }
                .keyboardShortcut("n")
            }
            CommandGroup(after: .newItem) {
                Button("打开配置") {
                    chatStore.openTomlConfigFile()
                }
                .keyboardShortcut("o")

                Button("保存配置") {
                    chatStore.saveTomlConfigFile()
                }
                .keyboardShortcut("s")
            }
        }
    }
}
