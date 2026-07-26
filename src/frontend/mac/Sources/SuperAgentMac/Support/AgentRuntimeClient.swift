import Foundation

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

struct RuntimeConversationMessage: Decodable, Sendable {
    var messageId: String
    var role: String
    var content: String
    var createdAt: Date
    var runId: String
    var runResult: AgentRuntimeResult?
}

struct RuntimeConversation: Decodable, Sendable {
    var conversationId: String
    var userId: String
    var agentName: String
    var title: String
    var createdAt: Date
    var updatedAt: Date
    var messages: [RuntimeConversationMessage]
}

private struct RuntimeConversationList: Decodable {
    var schemaVersion: Int
    var conversations: [RuntimeConversation]
}

private struct AgentRuntimeRequest: Encodable {
    var prompt: String
    var userID: String
    var conversationID: String

    private enum CodingKeys: String, CodingKey {
        case prompt
        case userID = "user_id"
        case conversationID = "conversation_id"
    }
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
    private static let processLock = NSLock()

    static func run(
        configText: String,
        prompt: String,
        userID: String,
        conversationID: String
    ) async throws -> AgentRuntimeResult {
        try await Task.detached(priority: .userInitiated) {
            let request = AgentRuntimeRequest(
                prompt: prompt,
                userID: userID,
                conversationID: conversationID
            )
            let data = try runProcess(
                configText: configText,
                arguments: ["run", "--request-stdin", "--output", "jsonl"],
                input: try JSONEncoder().encode(request)
            )
            return try readRunResult(from: data)
        }.value
    }

    static func listConversations(
        configText: String,
        userID: String
    ) async throws -> [RuntimeConversation] {
        try await Task.detached(priority: .userInitiated) {
            let data = try runProcess(
                configText: configText,
                arguments: ["conversations", "list", "--user-id", userID]
            )
            return try decodeJSON(RuntimeConversationList.self, from: data).conversations
        }.value
    }

    static func readConversation(
        configText: String,
        userID: String,
        conversationID: String
    ) async throws -> RuntimeConversation {
        try await readConversationCommand(
            configText: configText,
            arguments: [
                "conversations", "show",
                "--user-id", userID,
                "--conversation-id", conversationID,
            ]
        )
    }

    static func createConversation(
        configText: String,
        userID: String,
        title: String = ""
    ) async throws -> RuntimeConversation {
        try await readConversationCommand(
            configText: configText,
            arguments: [
                "conversations", "create",
                "--user-id", userID,
                "--title", title,
            ]
        )
    }

    static func renameConversation(
        configText: String,
        userID: String,
        conversationID: String,
        title: String
    ) async throws -> RuntimeConversation {
        try await readConversationCommand(
            configText: configText,
            arguments: [
                "conversations", "rename",
                "--user-id", userID,
                "--conversation-id", conversationID,
                "--title", title,
            ]
        )
    }

    static func clearConversation(
        configText: String,
        userID: String,
        conversationID: String
    ) async throws -> RuntimeConversation {
        try await readConversationCommand(
            configText: configText,
            arguments: [
                "conversations", "clear",
                "--user-id", userID,
                "--conversation-id", conversationID,
            ]
        )
    }

    static func deleteConversation(
        configText: String,
        userID: String,
        conversationID: String
    ) async throws {
        try await Task.detached(priority: .userInitiated) {
            _ = try runProcess(
                configText: configText,
                arguments: [
                    "conversations", "delete",
                    "--user-id", userID,
                    "--conversation-id", conversationID,
                ]
            )
        }.value
    }

    private static func readConversationCommand(
        configText: String,
        arguments: [String]
    ) async throws -> RuntimeConversation {
        try await Task.detached(priority: .userInitiated) {
            let data = try runProcess(configText: configText, arguments: arguments)
            return try decodeJSON(RuntimeConversation.self, from: data)
        }.value
    }

    private static func runProcess(
        configText: String,
        arguments: [String],
        input: Data = Data()
    ) throws -> Data {
        processLock.lock()
        defer { processLock.unlock() }
        let temporaryDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("super-agent-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(
            at: temporaryDirectory,
            withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: temporaryDirectory) }

        let configURL = temporaryDirectory.appendingPathComponent("agent.toml")
        try configText.write(to: configURL, atomically: true, encoding: .utf8)

        let process = Process()
        let standardInput = Pipe()
        let standardOutput = Pipe()
        let standardError = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [runtimeCommand()] + arguments + ["--config", configURL.path]
        process.standardInput = standardInput
        process.standardOutput = standardOutput
        process.standardError = standardError

        do {
            try process.run()
        } catch {
            throw AgentRuntimeClientError.commandFailed(error.localizedDescription)
        }
        standardInput.fileHandleForWriting.write(input)
        try standardInput.fileHandleForWriting.close()
        let outputData = standardOutput.fileHandleForReading.readDataToEndOfFile()
        let errorData = standardError.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()

        let errorText = String(data: errorData, encoding: .utf8) ?? ""
        guard process.terminationStatus == 0 else {
            throw AgentRuntimeClientError.commandFailed(
                errorText.trimmingCharacters(in: .whitespacesAndNewlines)
            )
        }
        return outputData
    }

    private static func readRunResult(from data: Data) throws -> AgentRuntimeResult {
        guard let text = String(data: data, encoding: .utf8) else {
            throw AgentRuntimeClientError.invalidOutput("输出不是 UTF-8")
        }
        for line in text.split(whereSeparator: { $0.isNewline }).reversed() {
            let output = try decodeJSON(AgentRuntimeOutputLine.self, from: Data(line.utf8))
            if output.type == "result", let result = output.result {
                return result
            }
        }
        throw AgentRuntimeClientError.invalidOutput("缺少 result 记录")
    }

    private static func decodeJSON<Value: Decodable>(
        _ type: Value.Type,
        from data: Data
    ) throws -> Value {
        do {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            decoder.dateDecodingStrategy = .custom { decoder in
                let container = try decoder.singleValueContainer()
                let text = try container.decode(String.self)
                guard let date = parseISO8601Date(text) else {
                    throw DecodingError.dataCorruptedError(
                        in: container,
                        debugDescription: "Invalid ISO 8601 date: \(text)"
                    )
                }
                return date
            }
            return try decoder.decode(type, from: data)
        } catch let error as AgentRuntimeClientError {
            throw error
        } catch {
            throw AgentRuntimeClientError.invalidOutput(error.localizedDescription)
        }
    }

    private static func parseISO8601Date(_ text: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: text) {
            return date
        }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: text)
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
