#!/bin/bash
# ============================================================
# 小虹 OpenClaw 一鍵部署腳本
# 用法: bash deploy-openclaw.sh [phase]
# 不指定 phase 則執行全部
# ============================================================

set -euo pipefail

PROJECT_DIR="$HOME/openclaw-xiaoh"
OPENCLAW_DIR="$HOME/openclaw"
OPENMANUS_DIR="$HOME/OpenManus"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
err() { echo "[ERROR] $*" >&2; exit 1; }

# ============================================================
# Phase 1: 倉庫設定
# ============================================================
phase1() {
    log "Phase 1: 倉庫設定"

    # Node.js 22 LTS
    if ! command -v node &>/dev/null || [[ $(node -v | cut -d. -f1 | tr -d 'v') -lt 22 ]]; then
        log "安裝 Node.js 22 LTS..."
        curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
        sudo apt-get install -y nodejs
    fi
    log "Node.js: $(node -v)"

    # pnpm
    if ! command -v pnpm &>/dev/null; then
        log "安裝 pnpm..."
        npm install -g pnpm
    fi

    # Clone OpenClaw (fork)
    if [ ! -d "$OPENCLAW_DIR" ]; then
        log "Clone OpenClaw..."
        gh repo fork anthropics/claude-code --clone --remote-name upstream -- "$OPENCLAW_DIR" || \
            git clone https://github.com/bigear88/xiaohong-openclaw.git "$OPENCLAW_DIR"
    fi

    # Clone OpenManus
    if [ ! -d "$OPENMANUS_DIR" ]; then
        log "Clone OpenManus..."
        git clone https://github.com/OpenManus/OpenManus.git "$OPENMANUS_DIR"
    fi

    # Python venv for MCP Bridge
    if [ ! -d "$PROJECT_DIR/.venv" ]; then
        log "建立 Python venv..."
        python3 -m venv "$PROJECT_DIR/.venv"
        "$PROJECT_DIR/.venv/bin/pip" install --upgrade pip
        "$PROJECT_DIR/.venv/bin/pip" install mcp psutil httpx playwright fastapi uvicorn websockets
        "$PROJECT_DIR/.venv/bin/playwright" install chromium
    fi

    log "Phase 1 完成 ✅"
}

# ============================================================
# Phase 2: MCP Bridge
# ============================================================
phase2() {
    log "Phase 2: MCP Bridge for Agents"
    log "xiaohong_agents_server.py 已在 mcp_servers/ 目錄"
    log "Phase 2 完成 ✅"
}

# ============================================================
# Phase 3: OpenManus 整合
# ============================================================
phase3() {
    log "Phase 3: OpenManus 整合 + 瀏覽器自動化"

    if [ -d "$OPENMANUS_DIR" ]; then
        cd "$OPENMANUS_DIR"
        if [ ! -d ".venv" ]; then
            python3 -m venv .venv
            .venv/bin/pip install -e .
        fi

        # 設定有頭瀏覽器（headless=false，避免被網站阻擋）
        mkdir -p config
        cat > config/config.toml << 'TOML'
[browser]
headless = false
disable_security = false

[llm]
model = "claude-sonnet-4-20250514"
api_key = "${ANTHROPIC_API_KEY}"
TOML
    fi

    # 安裝 Xvfb 虛擬顯示器（VM 上跑有頭瀏覽器用）
    if ! command -v Xvfb &>/dev/null; then
        log "安裝 Xvfb + x11vnc + noVNC..."
        sudo apt-get install -y xvfb x11vnc novnc
    fi

    # 啟動虛擬顯示器（如果不在桌面環境）
    if [ -z "${DISPLAY:-}" ]; then
        log "啟動 Xvfb 虛擬顯示器 :99..."
        sudo tee /etc/systemd/system/xvfb.service > /dev/null << 'EOF'
[Unit]
Description=Xvfb Virtual Display :99
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/Xvfb :99 -screen 0 1920x1080x24 -ac
Restart=always

[Install]
WantedBy=multi-user.target
EOF
        sudo systemctl daemon-reload
        sudo systemctl enable --now xvfb
    fi

    # 瀏覽器監控面板 systemd 服務
    sudo tee /etc/systemd/system/xiaohong-browser-monitor.service > /dev/null << EOF
[Unit]
Description=小虹瀏覽器自動化監控面板
After=network.target xvfb.service

[Service]
Type=simple
User=$USER
Environment="DISPLAY=:99"
ExecStart=$PROJECT_DIR/.venv/bin/python -m dashboard.browser_monitor
WorkingDirectory=$PROJECT_DIR
Restart=always
RestartSec=10
MemoryMax=200M

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable xiaohong-browser-monitor
    sudo systemctl start xiaohong-browser-monitor

    log "瀏覽器監控面板：http://localhost:8765"
    log "Phase 3 完成 ✅"
}

# ============================================================
# Phase 4: OpenClaw 設定
# ============================================================
phase4() {
    log "Phase 4: OpenClaw 設定"

    if [ -d "$OPENCLAW_DIR" ]; then
        cd "$OPENCLAW_DIR"
        pnpm install
        pnpm build
    fi

    # 複製設定
    mkdir -p "$HOME/.openclaw"
    cp "$PROJECT_DIR/openclaw-config/openclaw.json" "$HOME/.openclaw/config.json"

    # SKILL.md
    mkdir -p "$HOME/.openclaw/workspace/skills/xiaohong-persona"
    cp "$PROJECT_DIR/openclaw-config/SKILL.md" \
       "$HOME/.openclaw/workspace/skills/xiaohong-persona/SKILL.md"

    log "Phase 4 完成 ✅"
}

# ============================================================
# Phase 5: 系統監控
# ============================================================
phase5() {
    log "Phase 5: 系統監控"

    # systemd service for monitor daemon
    sudo tee /etc/systemd/system/xiaohong-monitor.service > /dev/null << EOF
[Unit]
Description=小虹系統監控 Daemon
After=network.target

[Service]
Type=simple
User=$USER
ExecStart=$PROJECT_DIR/.venv/bin/python -m mcp_servers.system_monitor_daemon
WorkingDirectory=$PROJECT_DIR
Restart=always
RestartSec=10
MemoryMax=100M

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable xiaohong-monitor
    sudo systemctl start xiaohong-monitor

    log "Phase 5 完成 ✅"
}

# ============================================================
# Phase 6: 排程
# ============================================================
phase6() {
    log "Phase 6: 排程設定"

    # 建立 cron jobs
    (crontab -l 2>/dev/null; cat << 'CRON'
# 小虹排程
30 7 * * * cd $HOME/openclaw-xiaoh && .venv/bin/python -c "from agents.briefing_agent import BriefingAgent; import asyncio; asyncio.run(BriefingAgent().newspaper_screenshot())" >> /tmp/xiaohong-cron.log 2>&1
0 8 * * * cd $HOME/openclaw-xiaoh && .venv/bin/python -c "from agents.briefing_agent import BriefingAgent; import asyncio; asyncio.run(BriefingAgent().morning_briefing())" >> /tmp/xiaohong-cron.log 2>&1
0 20 * * * cd $HOME/openclaw-xiaoh && .venv/bin/python -c "from agents.briefing_agent import BriefingAgent; import asyncio; asyncio.run(BriefingAgent().evening_briefing())" >> /tmp/xiaohong-cron.log 2>&1
CRON
    ) | sort -u | crontab -

    log "Phase 6 完成 ✅"
}

# ============================================================
# Phase 7: 測試驗證
# ============================================================
phase7() {
    log "Phase 7: 測試驗證"

    # 測試 MCP Bridge
    log "測試 MCP Bridge..."
    cd "$PROJECT_DIR"
    .venv/bin/python -c "
from mcp_servers.xiaohong_agents_server import guardian
status = guardian.status()
print(f'Memory: {status[\"memory_percent\"]}%')
print(f'Level: {status[\"guardian_level\"]}')
print('MCP Bridge OK ✅')
"

    # 測試 system monitor
    log "測試 System Monitor..."
    .venv/bin/python -c "
from mcp_servers.system_monitor_daemon import get_system_status
status = get_system_status()
print(f'CPU: {status[\"cpu_percent\"]}%')
print(f'RAM: {status[\"ram_percent\"]}%')
print('System Monitor OK ✅')
"

    log "Phase 7 完成 ✅"
    log "🎉 全部部署完成！"
}

# ============================================================
# 主程式
# ============================================================
main() {
    local phase="${1:-all}"

    log "小虹 OpenClaw 部署開始"
    log "專案目錄: $PROJECT_DIR"

    case "$phase" in
        1) phase1 ;;
        2) phase2 ;;
        3) phase3 ;;
        4) phase4 ;;
        5) phase5 ;;
        6) phase6 ;;
        7) phase7 ;;
        all)
            phase1
            phase2
            phase3
            phase4
            phase5
            phase6
            phase7
            ;;
        *) err "未知 phase: $phase" ;;
    esac
}

main "$@"
