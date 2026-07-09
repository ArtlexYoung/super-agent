// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "SuperAgentMac",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "SuperAgentMac", targets: ["SuperAgentMac"])
    ],
    targets: [
        .executableTarget(name: "SuperAgentMac")
    ]
)
