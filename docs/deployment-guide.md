# 小虹多Agent系統 — 部署與安裝指南

> 來源：Notion「🚀 小虹多Agent系統 — 部署與安裝指南」

## 一鍵安裝腳本 (setup_vm.sh)

安裝腳本自動完成以下步驟：

1. **系統套件** — curl, git, python3, nodejs, chromium, sqlite3, tmux, 中文字型, supervisor
2. **Node.js 20 LTS** — Claude Code CLI 需要 Node 18+
3. **Claude Code CLI** — `npm install -g @anthropic-ai/claude-code`
4. **Python 虛擬環境** — 所有 pip 依賴安裝在 `~/xiaohong/.venv/`
5. **SQLite 資料庫初始化** — 建立 tasks, agent_status, logs, daily_metrics 四張表
6. **systemd 服務** — xiaohong.service（主服務）+ xiaohong-dashboard.service（監控）
7. **防火牆** — 開放 SSH(22) + Dashboard(8080)
8. **config.yaml 模板** — 需手動填入 API keys

## Python 依賴清單

```
fastapi, uvicorn[standard], websockets
python-telegram-bot, httpx, aiohttp
edge-tts, playwright
apscheduler, notion-client
google-auth, google-auth-oauthlib, google-api-python-client
psutil, pydantic, pyyaml
rich, loguru, pillow
```

## 啟動步驟

### Step 1: 建立 VM
在 VirtualBox 中建立 Ubuntu 24.04 LTS Server VM（4核/8GB/60GB/Bridged）

### Step 2: 上傳程式碼
```bash
scp -r xiaohong/ user@VM_IP:~/
```

### Step 3: 執行安裝
```bash
chmod +x ~/xiaohong/scripts/setup_vm.sh
~/xiaohong/scripts/setup_vm.sh
```

### Step 4: 設定 API Keys
```bash
nano ~/xiaohong/config.yaml
```

### Step 5: Claude Code 認證
```bash
cd ~/xiaohong && source .venv/bin/activate
claude auth login
```

### Step 6: 啟動服務
```bash
sudo systemctl enable --now xiaohong
sudo systemctl enable --now xiaohong-dashboard
```

### Step 7: 驗證
- 開啟 `http://VM_IP:8080` 查看 Dashboard
- Telegram 傳 `/status` 確認連線
- 傳 `/help` 查看指令

## Telegram 指令表

| 指令 | 功能 |
|------|------|
| /status | 查看各 agent 狀態 |
| /morning | 手動觸發晨報 |
| /evening | 手動觸發晚報 |
| /newspaper | 手動截圖今日報紙 |
| /help | 指令說明 |

自然語言也可以直接對話，小虹會自動分派到對應 agent。

## 開發新 Agent 步驟

1. 在 `agents/` 下建立目錄和 `agent.py`
2. 繼承 `BaseAgent`，實作 `handle()` 方法
3. 在 `process_manager.py` 的 `AGENT_REGISTRY` 註冊
4. 在 `router.py` 的 `KEYWORD_RULES` 加入路由規則
5. 重啟服務：`sudo systemctl restart xiaohong`

## 開發優先級

1. ✅ VM 基礎 + Orchestrator 骨架
2. 🔲 晨報/晚報 Agent（已有經驗，可快速遷移）
3. 🔲 記帳 Agent（Notion DB 寫入）
4. 🔲 投資分析 Agent（Finviz 截圖 + 技術分析）
5. 🔲 新聞截圖 Agent（Playwright 去 Hami 截圖）
6. 🔲 健康管理 Agent（飲食記錄 + 卡路里）
7. 🔲 學習 Agent（LiveABC 進度）
8. 🔲 靈修 Agent（FHL MCP 整合）
