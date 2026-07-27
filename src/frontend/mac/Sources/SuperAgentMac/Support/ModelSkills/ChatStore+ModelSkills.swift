import Foundation

@MainActor
extension ChatStore {
    func refreshModelSkills() {
        let preferredName = selectedModelSkillDraft?.name
        let configText = makeRuntimeConfigText()
        Task {
            do {
                let profiles = try await AgentRuntimeClient.listModelSkills(
                    configText: configText
                )
                applyModelSkillProfiles(profiles, preferredName: preferredName)
                warningMessage = nil
            } catch {
                warningMessage = error.localizedDescription
            }
        }
    }

    func createModelSkill() {
        let draft = ModelSkillDraft(name: nextModelSkillName())
        modelSkillDrafts.append(draft)
        selectedModelSkillID = draft.id
        statusMessage = "已新建模型 Skill 草稿，保存后才会参与任务调度。"
    }

    func selectModelSkill(_ id: UUID) {
        guard modelSkillDrafts.contains(where: { $0.id == id }) else { return }
        selectedModelSkillID = id
    }

    func updateModelSkillDraft(
        _ id: UUID,
        change: (inout ModelSkillDraft) -> Void
    ) {
        guard let index = modelSkillDrafts.firstIndex(where: { $0.id == id }) else {
            return
        }
        change(&modelSkillDrafts[index])
    }

    func saveModelSkill(_ id: UUID) {
        guard let draft = modelSkillDrafts.first(where: { $0.id == id }) else {
            return
        }
        let credential = draft.credential.trimmingCharacters(in: .whitespacesAndNewlines)
        let environmentName = draft.apiKeyEnv.trimmingCharacters(in: .whitespacesAndNewlines)
        guard credential.isEmpty || !environmentName.isEmpty else {
            warningMessage = "填写密钥时必须同时填写密钥环境变量名。"
            return
        }
        let configText = makeRuntimeConfigText()
        Task {
            do {
                let saved = try await AgentRuntimeClient.saveModelSkill(
                    configText: configText,
                    userID: runtimeUserID,
                    request: draft.makeSaveRequest()
                )
                if !credential.isEmpty {
                    try ModelCredentialStore.save(
                        credential,
                        environmentName: environmentName
                    )
                }
                let profiles = try await AgentRuntimeClient.listModelSkills(
                    configText: configText
                )
                removeUnusedPreviousCredential(draft, profiles: profiles)
                applyModelSkillProfiles(
                    profiles,
                    preferredName: saved.name,
                    droppingDraftID: id
                )
                statusMessage = "已保存模型 Skill：model:\(saved.name)。"
                warningMessage = nil
            } catch {
                warningMessage = error.localizedDescription
            }
        }
    }

    func removeModelSkill(_ id: UUID) {
        guard let draft = modelSkillDrafts.first(where: { $0.id == id }) else {
            return
        }
        guard draft.isPersisted else {
            modelSkillDrafts.removeAll { $0.id == id }
            selectedModelSkillID = modelSkillDrafts.first?.id
            return
        }
        let configText = makeRuntimeConfigText()
        Task {
            do {
                try await AgentRuntimeClient.removeModelSkill(
                    configText: configText,
                    userID: runtimeUserID,
                    name: draft.previousName
                )
                let profiles = try await AgentRuntimeClient.listModelSkills(
                    configText: configText
                )
                removeCredentialIfUnused(draft.apiKeyEnv, profiles: profiles)
                applyModelSkillProfiles(profiles, preferredName: nil)
                statusMessage = "已删除模型 Skill：model:\(draft.previousName)。"
                warningMessage = nil
            } catch {
                warningMessage = error.localizedDescription
            }
        }
    }

    func clearModelSkillCredential(_ id: UUID) {
        guard let index = modelSkillDrafts.firstIndex(where: { $0.id == id }) else {
            return
        }
        do {
            try ModelCredentialStore.remove(
                environmentName: modelSkillDrafts[index].apiKeyEnv
            )
            let environmentName = modelSkillDrafts[index].apiKeyEnv
            for draftIndex in modelSkillDrafts.indices
            where modelSkillDrafts[draftIndex].apiKeyEnv == environmentName {
                modelSkillDrafts[draftIndex].credential = ""
                modelSkillDrafts[draftIndex].credentialStored = false
            }
            statusMessage = "已从 macOS 钥匙串删除模型密钥。"
            warningMessage = nil
            refreshModelSkills()
        } catch {
            warningMessage = error.localizedDescription
        }
    }

    private func applyModelSkillProfiles(
        _ profiles: [RuntimeModelSkillProfile],
        preferredName: String?,
        droppingDraftID: UUID? = nil
    ) {
        // Runtime data replaces persisted entries while untouched local drafts survive refreshes.
        let unsaved = modelSkillDrafts.filter {
            $0.source == "draft" && $0.id != droppingDraftID
        }
        modelSkillDrafts = profiles.map(ModelSkillDraft.init) + unsaved
        let selected = modelSkillDrafts.first { $0.name == preferredName }
            ?? modelSkillDrafts.first { $0.isPersisted && $0.isDefault }
            ?? modelSkillDrafts.first
        selectedModelSkillID = selected?.id
    }

    private func removeUnusedPreviousCredential(
        _ draft: ModelSkillDraft,
        profiles: [RuntimeModelSkillProfile]
    ) {
        let previous = draft.persistedApiKeyEnv
        guard previous != draft.apiKeyEnv else { return }
        removeCredentialIfUnused(previous, profiles: profiles)
    }

    private func removeCredentialIfUnused(
        _ environmentName: String,
        profiles: [RuntimeModelSkillProfile]
    ) {
        guard !environmentName.isEmpty,
              !profiles.contains(where: { $0.apiKeyEnv == environmentName }) else {
            return
        }
        try? ModelCredentialStore.remove(environmentName: environmentName)
    }

    private func nextModelSkillName() -> String {
        let existing = Set(modelSkillDrafts.map(\.name))
        for index in 1...999 {
            let name = String(format: "model-%02d", index)
            if !existing.contains(name) {
                return name
            }
        }
        return "model-\(UUID().uuidString.lowercased())"
    }
}
