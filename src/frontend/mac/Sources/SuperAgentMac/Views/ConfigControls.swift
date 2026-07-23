import AppKit
import SwiftUI

struct ToggleChoice: Identifiable {
    let id: String
    let title: String
    let help: String
}

struct ConfigSectionBox<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        GroupBox(title) {
            VStack(alignment: .leading, spacing: 14) {
                content
            }
            .padding(.top, 4)
        }
    }
}

struct SettingTextField: View {
    let title: String
    let help: String
    @Binding var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            FieldTitleView(title: title, help: help)
            TextField(title, text: $text)
        }
    }
}

struct SettingTextEditor: View {
    let title: String
    let help: String
    @Binding var text: String
    let height: CGFloat

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            FieldTitleView(title: title, help: help)
            TextEditor(text: $text)
                .font(.body)
                .frame(height: height)
                .padding(6)
                .background(Color(nsColor: .textBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        }
    }
}

struct SettingStringPicker: View {
    let title: String
    let help: String
    @Binding var selection: String
    let options: [String]
    var emptyTitle: String = ""

    private var visibleOptions: [String] {
        makeUniqueStrings(options + [selection])
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            FieldTitleView(title: title, help: help)
            Picker(title, selection: $selection) {
                ForEach(visibleOptions, id: \.self) { option in
                    Text(displayName(for: option)).tag(option)
                }
            }
            .pickerStyle(.menu)
            .frame(maxWidth: 360, alignment: .leading)
        }
    }

    private func displayName(for option: String) -> String {
        if option.isEmpty {
            return emptyTitle.isEmpty ? "空" : emptyTitle
        }
        return option
    }
}

struct ChoiceButtonListView: View {
    let choices: [ToggleChoice]
    let selectedID: String
    let buttonTitle: String
    let onSelect: (ToggleChoice) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(choices) { choice in
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(choice.title)
                            if selectedID == choice.id {
                                DefaultTagView(text: "默认")
                            }
                        }
                        Text(choice.help)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(selectedID == choice.id ? "已默认" : buttonTitle) {
                        onSelect(choice)
                    }
                    .disabled(selectedID == choice.id)
                }
            }
        }
    }
}

struct DirectoryListView: View {
    let title: String
    let help: String
    @Binding var items: [String]
    let defaultName: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            FieldTitleView(title: title, help: help)
            ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                HStack {
                    Text(item)
                        .font(.system(.body, design: .monospaced))
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Button("选择") {
                        chooseDirectory { items[index] = $0 }
                    }
                    Button {
                        items.remove(at: index)
                    } label: {
                        Image(systemName: "minus.circle")
                    }
                    .buttonStyle(.plain)
                    .help("删除这个目录")
                }
            }
            Button("添加目录") {
                chooseDirectory { items.append($0) }
            }
            .buttonStyle(.bordered)
            .help("选择一个\(title)")
        }
        .onAppear {
            if items.isEmpty {
                items = [defaultName]
            }
        }
    }
}

struct DirectoryPathView: View {
    let title: String
    let help: String
    @Binding var path: String
    let defaultName: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            FieldTitleView(title: title, help: help)
            HStack {
                Text(path.isEmpty ? defaultName : path)
                    .font(.system(.body, design: .monospaced))
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                Button("选择目录") {
                    chooseDirectory { path = $0 }
                }
            }
        }
    }
}

struct FieldTitleView: View {
    let title: String
    let help: String

    var body: some View {
        HStack(spacing: 6) {
            Text(title)
                .font(.caption.weight(.semibold))
            HelpHintView(help)
        }
    }
}

struct HelpHintView: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Image(systemName: "questionmark.circle")
            .font(.caption)
            .foregroundStyle(.secondary)
            .help(text)
    }
}

struct DefaultTagView: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.caption.weight(.semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Color.accentColor)
            .clipShape(Capsule())
    }
}

struct EmptyChoiceTextView: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(.secondary)
    }
}

func featureIsEnabled(_ name: String, config: AgentTomlConfig) -> Bool {
    config.agent.useFeatures.contains(name) && !config.agent.disableNames.contains(name)
}

func setFeatureEnabled(_ name: String, isEnabled: Bool, config: Binding<AgentTomlConfig>) {
    if isEnabled {
        addUnique(name, to: &config.wrappedValue.agent.useFeatures)
        removeValues([name], from: &config.wrappedValue.agent.disableNames)
    } else {
        removeValues([name], from: &config.wrappedValue.agent.useFeatures)
        addUnique(name, to: &config.wrappedValue.agent.disableNames)
    }
}

func skillIsOpen(_ name: String, capability: String, config: AgentTomlConfig) -> Bool {
    featureIsEnabled("skill", config: config)
        && config.agent.skills.contains(name)
        && skillCapabilityIsEnabled(capability, config: config)
        && !config.agent.disableNames.contains("\(capability):\(name)")
        && !config.agent.disableNames.contains(name)
}

func setSkillOpen(
    _ name: String,
    capability: String,
    isOpen: Bool,
    config: Binding<AgentTomlConfig>
) {
    if isOpen {
        setFeatureEnabled("skill", isEnabled: true, config: config)
        setSkillCapabilityEnabled(capability, isEnabled: true, config: config)
        addUnique(name, to: &config.wrappedValue.agent.skills)
        removeValues(
            ["\(capability):\(name)", name],
            from: &config.wrappedValue.agent.disableNames
        )
    } else {
        removeValues([name], from: &config.wrappedValue.agent.skills)
        addUnique("\(capability):\(name)", to: &config.wrappedValue.agent.disableNames)
    }
}

func memoryIsOpen(_ name: String, config: AgentTomlConfig) -> Bool {
    skillCapabilityIsEnabled("memory", config: config)
        && !config.agent.disableNames.contains("memory:\(name)")
        && !config.agent.disableNames.contains(name)
}

func setMemoryOpen(_ name: String, isOpen: Bool, config: Binding<AgentTomlConfig>) {
    if isOpen {
        setSkillCapabilityEnabled("memory", isEnabled: true, config: config)
        removeValues(["memory:\(name)", name], from: &config.wrappedValue.agent.disableNames)
    } else {
        addUnique("memory:\(name)", to: &config.wrappedValue.agent.disableNames)
    }
}

func skillCapabilityIsEnabled(_ capability: String, config: AgentTomlConfig) -> Bool {
    !config.agent.disableNames.contains(capability)
}

func setSkillCapabilityEnabled(
    _ capability: String,
    isEnabled: Bool,
    config: Binding<AgentTomlConfig>
) {
    if isEnabled {
        removeValues([capability], from: &config.wrappedValue.agent.disableNames)
    } else {
        addUnique(capability, to: &config.wrappedValue.agent.disableNames)
    }
}

func mcpIsOpen(_ name: String, config: AgentTomlConfig) -> Bool {
    featureIsEnabled("skill", config: config)
        && skillCapabilityIsEnabled("mcp", config: config)
        && config.agent.skills.contains(name)
        && !config.agent.disableNames.contains("mcp:\(name)")
        && !config.agent.disableNames.contains(name)
}

func setMCPOpen(_ name: String, isOpen: Bool, config: Binding<AgentTomlConfig>) {
    if isOpen {
        setFeatureEnabled("skill", isEnabled: true, config: config)
        setSkillCapabilityEnabled("mcp", isEnabled: true, config: config)
        addUnique(name, to: &config.wrappedValue.agent.skills)
        removeValues(["mcp:\(name)", name], from: &config.wrappedValue.agent.disableNames)
    } else {
        removeValues([name], from: &config.wrappedValue.agent.skills)
        addUnique("mcp:\(name)", to: &config.wrappedValue.agent.disableNames)
    }
}

func makeUniqueStrings(_ values: [String]) -> [String] {
    var seen: Set<String> = []
    var result: [String] = []
    for value in values {
        if !seen.contains(value) {
            seen.insert(value)
            result.append(value)
        }
    }
    return result
}

private func addUnique(_ value: String, to values: inout [String]) {
    if !values.contains(value) {
        values.append(value)
    }
}

private func removeValues(_ removedValues: [String], from values: inout [String]) {
    values.removeAll { removedValues.contains($0) }
}

private func chooseDirectory(_ onSelect: (String) -> Void) {
    let panel = NSOpenPanel()
    panel.canChooseFiles = false
    panel.canChooseDirectories = true
    panel.allowsMultipleSelection = false
    guard panel.runModal() == .OK, let url = panel.url else {
        return
    }
    onSelect(url.path)
}
