import Foundation

struct ProviderChatMessage: Codable, Equatable {
    var role: String
    var content: String
}

enum ModelChatClientError: LocalizedError {
    case missingAPIKeyEnv(String)
    case invalidURL(String)
    case badHTTPStatus(Int, String)
    case invalidResponse(String)
    case unknownProvider(String)

    var errorDescription: String? {
        switch self {
        case let .missingAPIKeyEnv(name):
            return "环境变量为空或未设置：\(name)"
        case let .invalidURL(url):
            return "模型服务地址无效：\(url)"
        case let .badHTTPStatus(status, body):
            return "模型服务返回 HTTP \(status)：\(body)"
        case let .invalidResponse(reason):
            return "模型响应无法解析：\(reason)"
        case let .unknownProvider(provider):
            return "未知 provider：\(provider)"
        }
    }
}

enum ModelChatClient {
    static func sendReply(config: AgentTomlConfig, messages: [ProviderChatMessage]) async throws -> String {
        let provider = config.model.provider.lowercased()
        if provider == "mock" {
            return makeMockReply(config: config, messages: messages)
        }
        if provider == "openai" || provider == "openai-compatible" {
            return try await sendOpenAICompatible(config: config, messages: messages)
        }
        if provider == "anthropic" || provider == "anthropic-compatible" {
            return try await sendAnthropicCompatible(config: config, messages: messages)
        }
        throw ModelChatClientError.unknownProvider(config.model.provider)
    }

    private static func makeMockReply(config: AgentTomlConfig, messages: [ProviderChatMessage]) -> String {
        let userText = messages.last { $0.role == "user" }?.content ?? ""
        return """
        已收到：「\(userText)」

        当前使用 TOML 模型配置：
        - provider: \(config.model.provider)
        - model: \(config.model.model)
        - workflow: \(config.agent.workflow)

        这是本地 mock 回复。要真实请求模型，把 [model].provider 改成 openai-compatible 或 anthropic-compatible，并填写 base_url 与 api_key_env。
        """
    }

    private static func sendOpenAICompatible(
        config: AgentTomlConfig,
        messages: [ProviderChatMessage]
    ) async throws -> String {
        let baseURL = config.model.baseURL.isEmpty ? "https://api.openai.com/v1" : config.model.baseURL
        let endpoint = try makeURL(baseURL: baseURL, path: "chat/completions")
        let payload: [String: Any] = [
            "model": config.model.model,
            "messages": messages.map { ["role": $0.role, "content": $0.content] }
        ]
        let data = try await sendJSONRequest(
            endpoint: endpoint,
            apiKey: try readAPIKey(config.model.apiKeyEnv),
            payload: payload
        )
        let object = try readJSONObject(from: data)
        guard let choices = object["choices"] as? [[String: Any]],
              let first = choices.first,
              let message = first["message"] as? [String: Any],
              let content = message["content"] as? String else {
            throw ModelChatClientError.invalidResponse("缺少 choices[0].message.content")
        }
        return content
    }

    private static func sendAnthropicCompatible(
        config: AgentTomlConfig,
        messages: [ProviderChatMessage]
    ) async throws -> String {
        let baseURL = config.model.baseURL.isEmpty ? "https://api.anthropic.com" : config.model.baseURL
        let endpoint = try makeURL(baseURL: baseURL, path: "v1/messages")
        let splitMessages = splitSystemMessage(messages)
        let payload: [String: Any] = [
            "model": config.model.model,
            "max_tokens": 4096,
            "system": splitMessages.system,
            "messages": splitMessages.messages.map { ["role": $0.role, "content": $0.content] }
        ]
        let data = try await sendJSONRequest(
            endpoint: endpoint,
            apiKey: try readAPIKey(config.model.apiKeyEnv),
            payload: payload
        )
        let object = try readJSONObject(from: data)
        guard let blocks = object["content"] as? [[String: Any]] else {
            throw ModelChatClientError.invalidResponse("缺少 content 文本块")
        }
        return blocks.compactMap { block in
            block["type"] as? String == "text" ? block["text"] as? String : nil
        }.joined()
    }

    private static func sendJSONRequest(
        endpoint: URL,
        apiKey: String,
        payload: [String: Any]
    ) async throws -> Data {
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "authorization")
        request.setValue(apiKey, forHTTPHeaderField: "x-api-key")
        request.setValue("application/json", forHTTPHeaderField: "content-type")
        request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw ModelChatClientError.invalidResponse("缺少 HTTP 响应")
        }
        guard (200..<300).contains(httpResponse.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw ModelChatClientError.badHTTPStatus(httpResponse.statusCode, body)
        }
        return data
    }

    private static func readAPIKey(_ envName: String) throws -> String {
        let name = envName.trimmingCharacters(in: .whitespacesAndNewlines)
        let value = ProcessInfo.processInfo.environment[name]?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, let value, !value.isEmpty else {
            throw ModelChatClientError.missingAPIKeyEnv(name.isEmpty ? "api_key_env" : name)
        }
        return value
    }

    private static func makeURL(baseURL: String, path: String) throws -> URL {
        let normalized = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: "\(normalized)/\(path)") else {
            throw ModelChatClientError.invalidURL("\(normalized)/\(path)")
        }
        return url
    }

    private static func readJSONObject(from data: Data) throws -> [String: Any] {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ModelChatClientError.invalidResponse("顶层 JSON 不是对象")
        }
        return object
    }

    private static func splitSystemMessage(
        _ messages: [ProviderChatMessage]
    ) -> (system: String, messages: [ProviderChatMessage]) {
        guard let first = messages.first, first.role == "system" else {
            return ("", messages)
        }
        return (first.content, Array(messages.dropFirst()))
    }
}
