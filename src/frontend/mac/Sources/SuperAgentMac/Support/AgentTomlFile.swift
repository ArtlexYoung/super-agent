import Foundation

enum AgentTomlFileError: LocalizedError {
    case invalidLine(Int, String)
    case invalidValue(String)

    var errorDescription: String? {
        switch self {
        case let .invalidLine(lineNumber, line):
            return "TOML 第 \(lineNumber) 行无法解析：\(line)"
        case let .invalidValue(value):
            return "TOML 值无法解析：\(value)"
        }
    }
}

enum AgentTomlFile {
    static func read(from url: URL) throws -> AgentTomlConfig {
        let text = try String(contentsOf: url, encoding: .utf8)
        return try parse(text)
    }

    static func write(_ config: AgentTomlConfig, to url: URL) throws {
        try makeTomlText(from: config).write(to: url, atomically: true, encoding: .utf8)
    }

    static func parse(_ text: String) throws -> AgentTomlConfig {
        let sections = try makeSectionMap(from: text)
        let fallback = AgentTomlConfig.defaultConfig
        let agentValues = sections["agent"] ?? [:]
        let modelValues = sections["model"] ?? [:]
        let pathValues = sections["paths"] ?? [:]
        let storageValues = sections["storage"] ?? [:]

        return AgentTomlConfig(
            agent: AgentTomlAgentSection(
                name: try readString(agentValues["name"]) ?? fallback.agent.name,
                system: try readString(agentValues["system"]) ?? fallback.agent.system,
                workflow: try readString(agentValues["workflow"]) ?? fallback.agent.workflow,
                memory: try readString(agentValues["memory"]) ?? fallback.agent.memory,
                skills: try readStringArray(agentValues["skills"]) ?? fallback.agent.skills,
                useFeatures: try readStringArray(agentValues["use_features"]) ?? fallback.agent.useFeatures,
                disableNames: try readStringArray(agentValues["disable_names"]) ?? fallback.agent.disableNames,
                maxAgentChainDepth: try readOptionalInt(agentValues["max_agent_chain_depth"])
            ),
            model: AgentTomlModelSection(
                provider: try readString(modelValues["provider"]) ?? fallback.model.provider,
                model: try readString(modelValues["model"]) ?? fallback.model.model,
                baseURL: try readString(modelValues["base_url"]) ?? fallback.model.baseURL,
                apiKeyEnv: try readString(modelValues["api_key_env"]) ?? fallback.model.apiKeyEnv
            ),
            paths: AgentTomlPathsSection(
                skills: try readStringArray(pathValues["skills"]) ?? fallback.paths.skills
            ),
            storage: AgentTomlStorageSection(
                backend: try readString(storageValues["backend"]) ?? fallback.storage.backend,
                path: try readString(storageValues["path"]) ?? fallback.storage.path,
                urlEnv: try readString(storageValues["url_env"]) ?? fallback.storage.urlEnv
            )
        )
    }

    static func makeTomlText(from config: AgentTomlConfig) -> String {
        var lines: [String] = []
        lines.append("[agent]")
        lines.append("name = \(quote(config.agent.name))")
        lines.append("system = \(quote(config.agent.system))")
        lines.append("workflow = \(quote(config.agent.workflow))")
        lines.append("memory = \(quote(config.agent.memory))")
        lines.append("skills = \(quoteArray(config.agent.skills))")
        lines.append("use_features = \(quoteArray(config.agent.useFeatures))")
        lines.append("disable_names = \(quoteArray(config.agent.disableNames))")
        if let maxDepth = config.agent.maxAgentChainDepth {
            lines.append("max_agent_chain_depth = \(maxDepth)")
        }
        lines.append("")
        lines.append("[model]")
        lines.append("provider = \(quote(config.model.provider))")
        lines.append("model = \(quote(config.model.model))")
        if !config.model.baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            lines.append("base_url = \(quote(config.model.baseURL))")
        }
        if !config.model.apiKeyEnv.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            lines.append("api_key_env = \(quote(config.model.apiKeyEnv))")
        }
        lines.append("")
        lines.append("[paths]")
        lines.append("skills = \(quoteArray(config.paths.skills))")
        lines.append("")
        lines.append("[storage]")
        lines.append("backend = \(quote(config.storage.backend))")
        lines.append("path = \(quote(config.storage.path))")
        if !config.storage.urlEnv.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            lines.append("url_env = \(quote(config.storage.urlEnv))")
        }
        lines.append("")
        return lines.joined(separator: "\n")
    }

    private static func makeSectionMap(from text: String) throws -> [String: [String: String]] {
        var currentSection = ""
        var sections: [String: [String: String]] = [:]
        for (offset, rawLine) in text.components(separatedBy: .newlines).enumerated() {
            let line = removeComment(from: rawLine).trimmingCharacters(in: .whitespacesAndNewlines)
            if line.isEmpty {
                continue
            }
            if line.hasPrefix("[") && line.hasSuffix("]") {
                currentSection = String(line.dropFirst().dropLast()).trimmingCharacters(in: .whitespaces)
                sections[currentSection, default: [:]] = sections[currentSection] ?? [:]
                continue
            }
            guard let equalIndex = line.firstIndex(of: "="), !currentSection.isEmpty else {
                throw AgentTomlFileError.invalidLine(offset + 1, rawLine)
            }
            let key = line[..<equalIndex].trimmingCharacters(in: .whitespaces)
            let valueStart = line.index(after: equalIndex)
            let value = line[valueStart...].trimmingCharacters(in: .whitespaces)
            sections[currentSection, default: [:]][String(key)] = String(value)
        }
        return sections
    }

    private static func readString(_ rawValue: String?) throws -> String? {
        guard let rawValue else {
            return nil
        }
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.hasPrefix("\""), value.hasSuffix("\"") {
            guard let data = value.data(using: .utf8),
                  let text = try? JSONDecoder().decode(String.self, from: data) else {
                throw AgentTomlFileError.invalidValue(value)
            }
            return text
        }
        if value.hasPrefix("'"), value.hasSuffix("'") {
            return String(value.dropFirst().dropLast())
        }
        return value
    }

    private static func readStringArray(_ rawValue: String?) throws -> [String]? {
        guard let rawValue else {
            return nil
        }
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard value.hasPrefix("["), value.hasSuffix("]") else {
            throw AgentTomlFileError.invalidValue(value)
        }
        let body = value.dropFirst().dropLast()
        let parts = splitTomlArrayItems(String(body))
        if parts.count == 1, parts[0].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return []
        }
        return try parts.map { try readString($0) ?? "" }
    }

    private static func readOptionalInt(_ rawValue: String?) throws -> Int? {
        guard let rawValue else {
            return nil
        }
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let number = Int(value), number > 0 else {
            throw AgentTomlFileError.invalidValue(value)
        }
        return number
    }

    private static func splitTomlArrayItems(_ text: String) -> [String] {
        var items: [String] = []
        var buffer = ""
        var quote: Character?
        var escaped = false
        for character in text {
            if escaped {
                buffer.append(character)
                escaped = false
                continue
            }
            if character == "\\", quote == "\"" {
                buffer.append(character)
                escaped = true
                continue
            }
            if character == "\"" || character == "'" {
                if quote == nil {
                    quote = character
                } else if quote == character {
                    quote = nil
                }
            }
            if character == ",", quote == nil {
                items.append(buffer)
                buffer = ""
            } else {
                buffer.append(character)
            }
        }
        items.append(buffer)
        return items
    }

    private static func removeComment(from line: String) -> String {
        var result = ""
        var quote: Character?
        var escaped = false
        for character in line {
            if escaped {
                result.append(character)
                escaped = false
                continue
            }
            if character == "\\", quote == "\"" {
                result.append(character)
                escaped = true
                continue
            }
            if character == "\"" || character == "'" {
                if quote == nil {
                    quote = character
                } else if quote == character {
                    quote = nil
                }
            }
            if character == "#", quote == nil {
                break
            }
            result.append(character)
        }
        return result
    }

    private static func quoteArray(_ values: [String]) -> String {
        "[" + values.map(quote).joined(separator: ", ") + "]"
    }

    private static func quote(_ value: String) -> String {
        if let data = try? JSONEncoder().encode(value),
           let text = String(data: data, encoding: .utf8) {
            return text
        }
        return "\"\(value)\""
    }
}
