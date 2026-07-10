import Foundation

struct ProviderChatMessage: Codable, Equatable, Sendable {
    var role: String
    var content: String
}

struct AgentRuntimeSubagentResult: Decodable, Sendable {
    var name: String
    var description: String
    var text: String
    var prompt: String
    var createdByAgent: Bool
    var subagentResults: [AgentRuntimeSubagentResult]?
    var runId: String
}

struct AgentRuntimeResult: Decodable, Sendable {
    var text: String
    var workflow: String
    var skills: [String]
    var subagentResults: [AgentRuntimeSubagentResult]?
    var warningMessages: [String]?
    var runId: String
    var stopReason: String
}

private struct AgentRuntimeRequest: Encodable {
    var prompt: String
    var messages: [ProviderChatMessage]
}

private struct AgentRuntimeOutputLine: Decodable {
    var type: String
    var result: AgentRuntimeResult?
}

enum AgentRuntimeClientError: LocalizedError {
    case commandFailed(String)
    case invalidOutput(String)

    var errorDescription: String? {
        switch self {
        case let .commandFailed(message):
            return "Super Agent runtime 调用失败：\(message)"
        case let .invalidOutput(message):
            return "Super Agent runtime 输出无效：\(message)"
        }
    }
}

enum AgentRuntimeClient {
    static func run(
        configText: String,
        prompt: String,
        messages: [ProviderChatMessage]
    ) async throws -> AgentRuntimeResult {
        try await Task.detached(priority: .userInitiated) {
            try runProcess(configText: configText, prompt: prompt, messages: messages)
        }.value
    }

    private static func runProcess(
        configText: String,
        prompt: String,
        messages: [ProviderChatMessage]
    ) throws -> AgentRuntimeResult {
        let temporaryDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("super-agent-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: temporaryDirectory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: temporaryDirectory) }

        let configURL = temporaryDirectory.appendingPathComponent("agent.toml")
        try configText.write(to: configURL, atomically: true, encoding: .utf8)
        let request = AgentRuntimeRequest(prompt: prompt, messages: messages)
        let requestData = try JSONEncoder().encode(request)

        let process = Process()
        let standardInput = Pipe()
        let standardOutput = Pipe()
        let standardError = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [
            runtimeCommand(),
            "run",
            "--config", configURL.path,
            "--request-stdin",
            "--output", "jsonl",
        ]
        process.standardInput = standardInput
        process.standardOutput = standardOutput
        process.standardError = standardError

        do {
            try process.run()
        } catch {
            throw AgentRuntimeClientError.commandFailed(error.localizedDescription)
        }
        standardInput.fileHandleForWriting.write(requestData)
        try standardInput.fileHandleForWriting.close()
        let outputData = standardOutput.fileHandleForReading.readDataToEndOfFile()
        let errorData = standardError.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()

        let errorText = String(data: errorData, encoding: .utf8) ?? ""
        guard process.terminationStatus == 0 else {
            throw AgentRuntimeClientError.commandFailed(errorText.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return try readResult(from: outputData)
    }

    private static func readResult(from data: Data) throws -> AgentRuntimeResult {
        guard let text = String(data: data, encoding: .utf8) else {
            throw AgentRuntimeClientError.invalidOutput("输出不是 UTF-8")
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        for line in text.split(whereSeparator: { $0.isNewline }).reversed() {
            let output = try decoder.decode(AgentRuntimeOutputLine.self, from: Data(line.utf8))
            if output.type == "result", let result = output.result {
                return result
            }
        }
        throw AgentRuntimeClientError.invalidOutput("缺少 result 记录")
    }

    private static func runtimeCommand() -> String {
        let configured = ProcessInfo.processInfo.environment["SUPER_AGENT_COMMAND"]?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if let configured, !configured.isEmpty {
            return configured
        }
        return "super-agent"
    }
}
