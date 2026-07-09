import Foundation

enum SkillManifestScanner {
    static func readSkillChoices(in roots: [URL], memoryURL: URL) -> [SkillManifestChoice] {
        let runtimeStats = readRuntimeStats(from: memoryURL)
        return roots.flatMap { readManifestChoices(in: $0, runtimeStats: runtimeStats) }
    }

    private static func readManifestChoices(
        in root: URL,
        runtimeStats: [String: SkillFreshnessStats]
    ) -> [SkillManifestChoice] {
        let fileManager = FileManager.default
        let directManifest = root.appendingPathComponent("skill.toml")
        if fileManager.fileExists(atPath: directManifest.path),
           let choice = readSkillChoice(from: directManifest, runtimeStats: runtimeStats) {
            return [choice]
        }
        guard let enumerator = fileManager.enumerator(
            at: root,
            includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles]
        ) else {
            return []
        }
        var choices: [SkillManifestChoice] = []
        // Python runtime 使用 root.rglob("skill.toml")；前端保持同样的递归扫描规则。
        for case let manifest as URL in enumerator where manifest.lastPathComponent == "skill.toml" {
            if let choice = readSkillChoice(from: manifest, runtimeStats: runtimeStats) {
                choices.append(choice)
            }
        }
        return choices
    }

    private static func readSkillChoice(
        from url: URL,
        runtimeStats: [String: SkillFreshnessStats]
    ) -> SkillManifestChoice? {
        let values = readManifestValues(from: url)
        guard let name = values["name"] else {
            return nil
        }
        let kind = (values["kind"] ?? "prompt").lowercased()
        let agentCreated = values["agent_created"] == "true"
        let agentCanUpdate = values["agent_can_update"].map { $0 == "true" } ?? agentCreated
        let freshness = Double(values["freshness"] ?? "") ?? 70
        let functionGroup = values["function_group"] ?? name
        let updatedAt = values["freshness_updated_at"] ?? ""
        let choice = SkillManifestChoice(
            name: name,
            kind: kind,
            agentCreated: agentCreated,
            agentCanUpdate: agentCanUpdate,
            freshness: freshness,
            functionGroup: functionGroup,
            freshnessUpdatedAt: updatedAt
        )
        return applyRuntimeStats(to: choice, runtimeStats: runtimeStats)
    }

    private static func readRuntimeStats(from memoryURL: URL) -> [String: SkillFreshnessStats] {
        let url = memoryURL.appendingPathComponent("skill_stats.json")
        guard let data = try? Data(contentsOf: url),
              let document = try? JSONDecoder().decode(SkillFreshnessDocument.self, from: data) else {
            return [:]
        }
        return document.skills
    }

    private static func applyRuntimeStats(
        to choice: SkillManifestChoice,
        runtimeStats: [String: SkillFreshnessStats]
    ) -> SkillManifestChoice {
        guard let stats = runtimeStats[choice.name] else {
            return choice
        }
        // 运行时统计覆盖静态保鲜度，这样 UI 展示的是最新使用结果。
        return SkillManifestChoice(
            name: choice.name,
            kind: choice.kind,
            agentCreated: choice.agentCreated,
            agentCanUpdate: choice.agentCanUpdate,
            freshness: stats.freshness ?? choice.freshness,
            functionGroup: stats.functionGroup ?? choice.functionGroup,
            freshnessUpdatedAt: stats.freshnessUpdatedAt ?? choice.freshnessUpdatedAt,
            callCount: stats.callCount ?? 0,
            successCount: stats.successCount ?? 0,
            replacementCount: stats.sameFunctionSuccessfulFollowups ?? 0,
            hasRuntimeStats: true
        )
    }

    private static func readManifestValues(from url: URL) -> [String: String] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            return [:]
        }
        var values: [String: String] = [:]
        let keys = [
            "name",
            "kind",
            "agent_created",
            "agent_can_update",
            "freshness",
            "function_group",
            "freshness_updated_at"
        ]
        for rawLine in text.components(separatedBy: .newlines) {
            let line = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
            guard let equalIndex = line.firstIndex(of: "=") else {
                continue
            }
            let key = String(line[..<equalIndex]).trimmingCharacters(in: .whitespacesAndNewlines)
            if keys.contains(key) {
                values[key] = cleanTomlValue(String(line[line.index(after: equalIndex)...]))
            }
        }
        return values
    }

    private static func cleanTomlValue(_ rawValue: String) -> String {
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.hasPrefix("\""), value.hasSuffix("\""),
           let data = value.data(using: .utf8),
           let text = try? JSONDecoder().decode(String.self, from: data) {
            return text
        }
        if value.hasPrefix("'"), value.hasSuffix("'") {
            return String(value.dropFirst().dropLast())
        }
        return value
    }
}

private struct SkillFreshnessDocument: Decodable {
    var skills: [String: SkillFreshnessStats]
}

private struct SkillFreshnessStats: Decodable {
    var freshness: Double?
    var functionGroup: String?
    var freshnessUpdatedAt: String?
    var callCount: Int?
    var successCount: Int?
    var sameFunctionSuccessfulFollowups: Int?

    private enum CodingKeys: String, CodingKey {
        case freshness
        case functionGroup = "function_group"
        case freshnessUpdatedAt = "freshness_updated_at"
        case callCount = "call_count"
        case successCount = "success_count"
        case sameFunctionSuccessfulFollowups = "same_function_successful_followups"
    }
}
