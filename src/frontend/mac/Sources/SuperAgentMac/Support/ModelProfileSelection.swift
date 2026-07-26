import Foundation

func makeInitialSelectedModelProfileID(
    requestedID: UUID?,
    profiles: [ModelProfile]
) -> UUID? {
    let fallbackID = profiles.first?.id
    guard let requestedID else {
        return fallbackID
    }
    return profiles.contains { $0.id == requestedID } ? requestedID : fallbackID
}

func applySelectedModelProfile(
    selectedID: UUID?,
    profiles: [ModelProfile],
    config: AgentTomlConfig
) -> AgentTomlConfig {
    var nextConfig = config
    if let selectedID,
       let profile = profiles.first(where: { $0.id == selectedID }) {
        nextConfig.model = profile.settings
    }
    return nextConfig
}

func modelProfileTitle(_ model: AgentTomlModelSection) -> String {
    model.model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        ? model.provider
        : model.model
}
