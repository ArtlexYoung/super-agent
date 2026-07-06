#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
VERSION="$(
    python3 - "${REPO_ROOT}/pyproject.toml" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
if not match:
    raise SystemExit("version not found in pyproject.toml")
print(match.group(1))
PY
)"

APP_NAME="SuperAgentMac"
BUILD_DIR="${SCRIPT_DIR}/.build/release"
RELEASE_DIR="${REPO_ROOT}/release/mac"
APP_DIR="${RELEASE_DIR}/${APP_NAME}.app"
ZIP_PATH="${RELEASE_DIR}/${APP_NAME}-${VERSION}-macOS.zip"

swift build -c release --package-path "${SCRIPT_DIR}"

rm -rf "${APP_DIR}"
mkdir -p "${APP_DIR}/Contents/MacOS" "${APP_DIR}/Contents/Resources"
cp "${BUILD_DIR}/${APP_NAME}" "${APP_DIR}/Contents/MacOS/${APP_NAME}"
chmod +x "${APP_DIR}/Contents/MacOS/${APP_NAME}"

cat > "${APP_DIR}/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>zh_CN</string>
    <key>CFBundleExecutable</key>
    <string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>dev.super-agent.mac</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>Super Agent</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>${VERSION}</string>
    <key>CFBundleVersion</key>
    <string>${VERSION}</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

cp "${SCRIPT_DIR}/README.md" "${APP_DIR}/Contents/Resources/README.md"
if [[ -f "${REPO_ROOT}/examples/basic/agent.toml" ]]; then
    cp "${REPO_ROOT}/examples/basic/agent.toml" "${APP_DIR}/Contents/Resources/default-agent.toml"
fi

rm -f "${ZIP_PATH}"
(
    cd "${RELEASE_DIR}"
    ditto --norsrc -c -k --keepParent "${APP_NAME}.app" "${ZIP_PATH}"
)

cat > "${RELEASE_DIR}/README.md" <<EOF
# Super Agent Mac Release

版本：${VERSION}

内容：

- \`${APP_NAME}.app\`：macOS SwiftUI 对话页面。
- \`${APP_NAME}-${VERSION}-macOS.zip\`：带版本号的压缩包。

运行：

\`\`\`bash
open release/mac/${APP_NAME}.app
\`\`\`

如果 macOS Gatekeeper 拦截本地未签名 app，可以在 Finder 里右键 app 后选择“打开”。
EOF

echo "${ZIP_PATH}"
