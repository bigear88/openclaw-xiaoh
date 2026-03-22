#!/usr/bin/env bash
# Clawalytics 安裝腳本
# 解決 better-sqlite3 在 Windows + Node 24 的編譯問題
set -e

echo "=== Clawalytics Setup ==="
echo ""

# 檢查 Node.js 版本
NODE_VER=$(node -v)
echo "Node.js: $NODE_VER"

# 方案 1: 嘗試正常安裝
echo ""
echo "[1/3] 嘗試 npm install..."
if npm install 2>/dev/null; then
    echo "✓ npm install 成功"
else
    echo "✗ npm install 失敗（可能缺少 C++ 編譯工具）"
    echo ""
    echo "[2/3] 嘗試 --ignore-scripts 安裝..."
    npm install --ignore-scripts

    echo ""
    echo "[3/3] 嘗試取得 better-sqlite3 prebuilt binary..."
    cd node_modules/better-sqlite3
    if npx prebuild-install 2>/dev/null; then
        echo "✓ Prebuilt binary 安裝成功"
    else
        echo ""
        echo "========================================="
        echo "  需要安裝 Visual Studio Build Tools"
        echo "========================================="
        echo ""
        echo "選項 A: 安裝 VS Build Tools（推薦）"
        echo "  winget install Microsoft.VisualStudio.2022.BuildTools --override \"--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended\""
        echo "  然後重新執行: npm install"
        echo ""
        echo "選項 B: 降級 Node.js 到 v22（有 prebuilt binary）"
        echo "  nvm install 22"
        echo "  nvm use 22"
        echo "  npm install"
        echo ""
        echo "選項 C: 使用全域安裝（如果其他環境已編譯）"
        echo "  npm install -g clawalytics"
        echo ""
        exit 1
    fi
    cd ../..
fi

echo ""
echo "=== 設定 Clawalytics Config ==="
mkdir -p ~/.clawalytics
if [ ! -f ~/.clawalytics/config.yaml ]; then
    cat > ~/.clawalytics/config.yaml << 'EOF'
logPath: ~/.claude/projects
openClawEnabled: true
openClawPath: ~/.openclaw
securityAlertsEnabled: true
gatewayLogsPath: /tmp/openclaw
EOF
    echo "✓ 已建立 ~/.clawalytics/config.yaml"
else
    echo "✓ config.yaml 已存在"
fi

echo ""
echo "=== 完成 ==="
echo "啟動方式："
echo "  npx clawalytics start --port 9174"
echo "  或 npm run analytics"
echo ""
echo "Dashboard 整合："
echo "  python dashboard/browser_monitor.py"
echo "  然後開啟 http://localhost:8765 → 成本分析分頁"
