import Foundation

struct RuntimeRunInsight: Decodable, Equatable, Sendable {
    var schemaVersion: Int
    var snapshot: RuntimeRunSnapshot
    var schedule: RuntimeTaskSchedule
    var taskPlan: RuntimeTaskPlan
    var taskSteps: [RuntimePlannedTaskStep]
    var modelCalls: [RuntimeModelCall]
    var routingEvidence: [RuntimeModelRoutingEvidence]
    var skillFreshness: [RuntimeSkillFreshness]
    var evolution: [RuntimeSkillEvolution]
}

struct RuntimeRunSnapshot: Decodable, Equatable, Sendable {
    var runId: String
    var agentName: String
    var status: String
    var workflow: String?
    var usedSkills: [String]
    var stopReason: String?
}

struct RuntimeTaskSchedule: Decodable, Equatable, Sendable {
    var purpose: String?
    var requiredFeatures: [String]?
    var workflow: String?
    var models: [RuntimeScheduledModel]?
    var skills: [String]?
    var subagents: [String]?
    var subagentReasons: [String]?
    var executionMode: String?
    var planner: String?
    var planningReasons: [String]?
}

struct RuntimeTaskPlan: Decodable, Equatable, Sendable {
    var planner: String?
    var reasons: [String]?
}

struct RuntimePlannedTaskStep: Decodable, Equatable, Sendable, Identifiable {
    var step: Int
    var instruction: String
    var purpose: String
    var requiredFeatures: [String]
    var workflow: String
    var models: [RuntimeScheduledModel]
    var skills: [String]
    var subagents: [String]
    var subagentReasons: [String]
    var status: String
    var text: String?
    var stopReason: String?

    var id: Int { step }
}

struct RuntimeScheduledModel: Decodable, Equatable, Sendable, Identifiable {
    var key: String
    var model: String
    var score: Double
    var reasons: [String]

    var id: String { key }
}

struct RuntimeModelCall: Decodable, Equatable, Sendable, Identifiable {
    var callId: Int
    var attempt: Int
    var profile: String?
    var model: String?
    var purpose: String?
    var status: String
    var score: Double?
    var reasons: [String]?
    var latencyMs: Int?
    var inputTokens: Int?
    var outputTokens: Int?
    var estimatedCost: Double?
    var errorType: String?
    var message: String?
    var willFallback: Bool?

    var id: Int { callId }
}

struct RuntimeModelRoutingEvidence: Decodable, Equatable, Sendable, Identifiable {
    var profileKey: String
    var purpose: String
    var callCount: Int
    var successCount: Int
    var reliability: Double
    var averageQuality: Double
    var averageLatencyMs: Double
    var averageInputTokens: Double
    var averageOutputTokens: Double
    var averageCost: Double

    var id: String { "\(purpose):\(profileKey)" }
}

struct RuntimeSkillFreshness: Decodable, Equatable, Sendable, Identifiable {
    var skill: String
    var functionGroup: String
    var freshness: Double
    var callCount: Int
    var successCount: Int
    var errorCount: Int
    var totalInputTokens: Int
    var totalOutputTokens: Int
    var sameFunctionSuccessfulFollowups: Int
    var lastUsedAt: String

    var id: String { skill }
}

struct RuntimeSkillEvolution: Decodable, Equatable, Sendable, Identifiable {
    var evolutionId: String
    var skillKey: String
    var status: String
    var sourceRevision: RuntimeSkillRevision?
    var candidateRevision: RuntimeSkillRevision?
    var reasonCodes: [String]
    var reasons: [String]
    var goal: String
    var candidateId: String
    var evaluationScore: Double?
    var detail: String

    var id: String { evolutionId }
}

struct RuntimeSkillRevision: Decodable, Equatable, Sendable {
    var key: String
    var version: String
}
