import Foundation

func findAgentRunNode(_ id: UUID, in nodes: [AgentRunNode]) -> AgentRunNode? {
    for node in nodes {
        if node.id == id {
            return node
        }
        if let child = findAgentRunNode(id, in: node.children) {
            return child
        }
    }
    return nil
}
