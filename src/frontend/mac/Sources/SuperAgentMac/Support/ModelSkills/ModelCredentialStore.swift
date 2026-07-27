import Foundation
import Security

enum ModelCredentialStoreError: LocalizedError {
    case keychain(OSStatus)

    var errorDescription: String? {
        switch self {
        case let .keychain(status):
            let message = SecCopyErrorMessageString(status, nil) as String? ?? "未知错误"
            return "保存模型密钥失败：\(message)"
        }
    }
}

enum ModelCredentialStore {
    private static let service = "dev.super-agent.mac.model"
    private static let accountIndexKey = "model-credential-environment-names"

    static func save(_ secret: String, environmentName: String) throws {
        let name = environmentName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else {
            throw CocoaError(.validationMissingMandatoryProperty)
        }
        let cleanSecret = secret.trimmingCharacters(in: .whitespacesAndNewlines)
        if cleanSecret.isEmpty {
            try remove(environmentName: name)
            return
        }
        let query = baseQuery(name)
        SecItemDelete(query as CFDictionary)
        var item = query
        item[kSecValueData as String] = Data(cleanSecret.utf8)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        let status = SecItemAdd(item as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw ModelCredentialStoreError.keychain(status)
        }
        var names = storedEnvironmentNames()
        names.insert(name)
        UserDefaults.standard.set(Array(names).sorted(), forKey: accountIndexKey)
    }

    static func remove(environmentName: String) throws {
        let name = environmentName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        let status = SecItemDelete(baseQuery(name) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw ModelCredentialStoreError.keychain(status)
        }
        var names = storedEnvironmentNames()
        names.remove(name)
        UserDefaults.standard.set(Array(names).sorted(), forKey: accountIndexKey)
    }

    static func contains(_ environmentName: String) -> Bool {
        read(environmentName) != nil
    }

    static func processEnvironment() -> [String: String] {
        // UserDefaults indexes names only; secret values remain in Keychain.
        Dictionary(
            uniqueKeysWithValues: storedEnvironmentNames().compactMap { name in
                read(name).map { (name, $0) }
            }
        )
    }

    private static func read(_ environmentName: String) -> String? {
        let name = environmentName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return nil }
        var query = baseQuery(name)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else {
            return nil
        }
        return String(data: data, encoding: .utf8)
    }

    private static func baseQuery(_ environmentName: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: environmentName,
        ]
    }

    private static func storedEnvironmentNames() -> Set<String> {
        Set(UserDefaults.standard.stringArray(forKey: accountIndexKey) ?? [])
    }
}
